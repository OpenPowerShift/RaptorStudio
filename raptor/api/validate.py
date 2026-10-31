"""Lightweight pre-flight validation for the MCP side.

Structural rules are delegated to ``core/fileio/builder.validate()``.
This module adds distribution-parameter-range checks and unconditional
engine gates that the builder's structural validator does not cover.

The distribution-parameter rules themselves live in
``core/distro_rules.py`` -- the one owner, promoted out of
``gui/ui/inspector.py`` (review D-04). This module used to carry its own
copy, byte-identical in code to the Inspector's and differing only in
comments; two copies of a 22-distribution rule table cannot be kept in
sync, and the API's had already lost the explanatory comments. Both doors
now call the same function, so their findings cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from raptor.core.distro_rules import (
    validate_distro_params,
)
from raptor.core.fileio.rbd import RbdFile


@dataclass
class Issue:
    """One validation finding."""
    severity: str          # "error" or "warning"
    element: str           # human-readable element name (e.g. "Block 5 'PumpA'")
    message: str           # what's wrong and how to fix it

    def __str__(self) -> str:
        tag = "ERROR" if self.severity == "error" else "WARN"
        return f"{tag}: {self.element}: {self.message}"


# ---------------------------------------------------------------------------
# Distribution parameter validation
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def rbds_schema() -> dict:
    """The `.rbds` JSON Schema that opening and validating a file check
    against (`raptor.core.fileio.rbds_schema`)."""
    from raptor.core.fileio.rbds_schema import build_schema
    return build_schema()


def issues(rbd: RbdFile) -> list[Issue]:
    """Return all validation findings for *rbd*.

    Combines:
    1. Structural rules from ``builder.validate()``.
    2. Distribution parameter range checks for blocks, events, phases
       and triggers.
    3. Unconditional engine gates (unsupported SimStart, out-of-range
       GDType, fail_mean-undefined combos).
    """
    result: list[Issue] = []

    # --- 0. The .rbds schema check's warnings (keys it kept but ignores) ---
    for msg in getattr(rbd, "format_warnings", None) or []:
        result.append(Issue("warning", "file", msg))

    # --- 1. Structural rules ---
    from raptor.core.fileio.builder import validate as structural_validate
    for msg in structural_validate(rbd):
        if msg.startswith("ERROR"):
            result.append(Issue("error", "model", msg))
        else:
            result.append(Issue("warning", "model", msg))

    # --- 2. Distribution parameter ranges ---
    result.extend(_check_block_dist_params(rbd))
    result.extend(_check_event_dist_params(rbd))
    result.extend(_check_section_dist_params(rbd, "PHASES", "PhaseName", "Phase"))
    result.extend(_check_section_dist_params(rbd, "TRIGGERS", "TriggerName", "Trigger"))

    # --- 3. Unconditional engine gates ---
    result.extend(_check_sim_start_types(rbd))
    result.extend(_check_gd_types(rbd))
    result.extend(_check_named_references(rbd))
    _check_fail_mean_undefined(rbd, result)

    result.extend(_check_duplicate_names(rbd))

    # --- 4. Raptor 7's own input rules: what it refuses to run, to draw, or
    # to accept in a dialog (raptor/core/input_rules.py; it replaced this
    # module's own fan-in/fan-out and isolated-node checks) ---
    result.extend(_raptor7_input_rules(rbd))

    # --- 5. Topology / capacity gates ---
    result.extend(_check_node_k_over_n(rbd))
    result.extend(_check_hierarchy_parents(rbd))
    result.extend(_check_dependency_targets(rbd))
    result.extend(_check_unrunnable_model(rbd))
    result.extend(_check_capacity_settings(rbd))
    result.extend(_check_start_to_end_reachable(rbd))
    result.extend(_check_link_cycle(rbd))

    return result


def _check_hierarchy_parents(rbd: RbdFile) -> list[Issue]:
    """Every child's parent must still exist.

    Deleting a hierarchy row used to leave its blocks/nodes/events
    pointing at a missing parent; the model validated as runnable and the
    engine then failed with a bare numeric key.
    """
    hier_section = rbd.sections.get("HIERARCHY_DATA")
    if hier_section is None:
        return []
    known = {str(h.get("ID")) for h in hier_section.rows}
    out: list[Issue] = []
    for sec_name, parent_col, label in (
            ("BLOCK_DATA", "ParentId", "block"),
            ("NODE_DATA", "parent", "node"),
            ("EVENT_DATA", "ParentId", "event"),
            ("HIERARCHY_DATA", "Parent", "hierarchy")):
        sec = rbd.sections.get(sec_name)
        if sec is None:
            continue
        for row in sec.rows:
            parent = str(row.get(parent_col, "0"))
            if parent in ("", "0", "None") or parent in known:
                continue
            name = (row.get("blockName") or row.get("Name")
                    or row.get("eventName") or row.get("hierarchyName")
                    or row.get("ID"))
            out.append(Issue(
                "error", "model",
                f"{label} {name!r} names missing hierarchy {parent!r}"))
    return out


def _check_link_cycle(rbd: RbdFile) -> list[Issue]:
    """An ordinary RBD link cycle cannot be simulated.

    Dependency cycles are legal (separate warning rule); a cycle in
    LINK_DATA is not. Without this the model validates as runnable and the
    engine walks forever (or raises RecursionError).
    """
    sec = rbd.sections.get("LINK_DATA")
    if sec is None:
        return []
    graph: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for row in sec.rows:
        src = (str(row.get("FromType", "")), str(row.get("FromID", "")))
        dst = (str(row.get("ToType", "")), str(row.get("ToID", "")))
        graph.setdefault(src, []).append(dst)
    state: dict[tuple[str, str], int] = {}
    path: list[tuple[str, str]] = []

    # Iterative depth-first search. Recursive DFS overflowed Python's
    # recursion limit on a long series chain (a 1000-block model has a
    # 1000-node path), turning "validate" into an internal error before
    # any simulation could start.
    for start in graph:
        if state.get(start, 0) != 0:
            continue
        state[start] = 1
        path.append(start)
        stack = [(start, iter(graph.get(start, ())))]
        while stack:
            node, children = stack[-1]
            descended = False
            for nxt in children:
                nxt_state = state.get(nxt, 0)
                if nxt_state == 1:
                    begin = path.index(nxt)
                    cycle = path[begin:] + [nxt]
                    found = " -> ".join(f"{k} {i}" for k, i in cycle)
                    return [Issue("error", "model",
                                  f"Link cycle ({found}) cannot be simulated")]
                if nxt_state == 0:
                    state[nxt] = 1
                    path.append(nxt)
                    stack.append((nxt, iter(graph.get(nxt, ()))))
                    descended = True
                    break
            if not descended:
                path.pop()
                state[node] = 2
                stack.pop()
    return []


# ---------------------------------------------------------------------------
# Block distribution parameters
# ---------------------------------------------------------------------------

def _check_empirical(row: dict, key: str, element: str, use: str
                     ) -> list[Issue]:
    """Each Empirical data point must be between 0 and 999,999,999.999999,
    the range Raptor 7's data dialog accepts (Accept Data)."""
    try:
        points = [float(p) for p in str(row.get(key, "")).split()]
    except ValueError:
        return [Issue("error", element,
                      f"{element} {use} Empirical data is not all numbers")]
    # No points at all is `core.input_rules`' (block.empirical_points).
    bad = [p for p in points if not 0.0 <= p < 999999999.999999]
    if bad:
        return [Issue("error", element,
                      f"{element} {use} Empirical data point {bad[0]:g} is "
                      f"outside 0 to 999,999,999.999999")]
    return []


def _units(rbd: RbdFile) -> str:
    """The model's time unit, for the out-of-range hint."""
    from raptor.api.run import model_units
    return model_units(rbd)


def _check_block_dist_params(rbd: RbdFile) -> list[Issue]:
    """Check fail/repair distribution parameter ranges for every block."""
    result: list[Issue] = []
    sec = rbd.sections.get("BLOCK_DATA")
    if not sec:
        return result

    for row in sec.rows:
        try:
            bid = int(float(row.get("ID", "0")))
        except (ValueError, TypeError):
            continue
        if bid == 0:
            continue
        name = row.get("blockName") or f"Block {bid}"
        element = f"Block {bid} '{name}'"

        # Fail distribution
        try:
            dist = int(float(row.get("FDist", "0")))
            params = [float(row.get(f"FParam{i}", "0")) for i in (1, 2, 3)]
            n_params = int(float(row.get("#FParam", "0")))
        except (ValueError, TypeError):
            continue
        if dist == 16:
            result += _check_empirical(row, "FEmp", element, "fail")
        elif dist != 0 and n_params > 0:
            err = validate_distro_params(dist, params[:n_params], f"{element} fail",
                                         _units(rbd))
            if err:
                result.append(Issue("error", element, err))

        # Repair distribution
        try:
            dist = int(float(row.get("RDist", "0")))
            params = [float(row.get(f"RParam{i}", "0")) for i in (1, 2, 3)]
            n_params = int(float(row.get("#RParam", "0")))
        except (ValueError, TypeError):
            continue
        if dist == 16:
            result += _check_empirical(row, "REmp", element, "repair")
        elif dist != 0 and n_params > 0:
            err = validate_distro_params(dist, params[:n_params], f"{element} repair",
                                         _units(rbd))
            if err:
                result.append(Issue("error", element, err))

    return result


# ---------------------------------------------------------------------------
# Event distribution parameters
# ---------------------------------------------------------------------------

def _check_event_dist_params(rbd: RbdFile) -> list[Issue]:
    """Check the success-probability range for every event.

    Unlike blocks, events don't carry ``Param1..3`` columns -- `make_event_row`
    (rbdlib.py) stores a single Bernoulli success probability in
    ``failVal`` with ``Distro`` fixed at ``D_BERNOULLI`` (17, "P Success").
    Reading ``Param1..3`` here always saw the column-default ``0.0`` and
    rejected every valid probability.
    """
    result: list[Issue] = []
    sec = rbd.sections.get("EVENT_DATA")
    if not sec:
        return result

    for row in sec.rows:
        try:
            eid = int(float(row.get("ID", "0")))
        except (ValueError, TypeError):
            continue
        if eid == 0:
            continue
        name = row.get("eventName") or f"Event {eid}"
        element = f"Event {eid} '{name}'"

        try:
            dist = int(float(row.get("Distro", "0")))
            prob = float(row.get("failVal", "0"))
        except (ValueError, TypeError):
            continue
        if dist != 0:
            err = validate_distro_params(dist, [prob], f"{element}", _units(rbd))
            if err:
                result.append(Issue("error", element, err))

    return result


# ---------------------------------------------------------------------------
# Phase/Trigger distribution parameters
# ---------------------------------------------------------------------------

def _check_section_dist_params(rbd: RbdFile, section_name: str,
                               name_col: str, label: str) -> list[Issue]:
    """Check distribution parameters for phases or triggers."""
    result: list[Issue] = []
    sec = rbd.sections.get(section_name)
    if not sec:
        return result

    for row in sec.rows:
        try:
            dist = int(float(row.get("Dist", "0")))
            params = [float(row.get(f"Param{i}", "0")) for i in (1, 2, 3)]
        except (ValueError, TypeError):
            continue
        if dist == 0:
            continue
        row_name = row.get(name_col) or f"{label} (unnamed)"
        element = f"{label} '{row_name}'"
        err = validate_distro_params(dist, params, label, _units(rbd))
        if err:
            result.append(Issue("error", element, err))

    return result


# ---------------------------------------------------------------------------
# Unconditional engine gates
# ---------------------------------------------------------------------------

def _check_sim_start_types(rbd: RbdFile) -> list[Issue]:
    """SimStart types the engine does not implement (anything outside 1..6).

    The rule is `block_lifecycle.START_TYPES`, the set `get_start_cond`
    itself accepts; every type in it has Raptor 7 captures (`SimStartProbe*`,
    `SimStartLdtProbe`, `DepDownStartProbe`).
    """
    from raptor.core.engine.block_lifecycle import START_TYPES
    result: list[Issue] = []
    sec = rbd.sections.get("BLOCK_DATA")
    if not sec:
        return result

    for row in sec.rows:
        try:
            bid = int(float(row.get("ID", "0")))
        except (ValueError, TypeError):
            continue
        if bid == 0:
            continue
        try:
            sim_start = int(float(row.get("SimStart", "3")))
        except (ValueError, TypeError):
            sim_start = 3
        if sim_start not in START_TYPES:
            name = row.get("blockName") or f"Block {bid}"
            result.append(Issue(
                "error",
                f"Block {bid} '{name}'",
                f"SimStart type {sim_start} is not a Raptor start type "
                f"(valid: 1 to 6)",
            ))
    return result


def _check_named_references(rbd: RbdFile) -> list[Issue]:
    """A block that names a spare pool, resource or trigger which does not exist.

    The engine cannot resolve a pooled spare without its pool (it raised
    `EngineInvariantError` mid-run), and a dangling resource or trigger
    reference silently changed how the block behaved.  All three are
    reported before Run instead.  `SpType` is compared case-insensitively:
    `configure_sparing` used to write `pool` while the check looked for
    `Pooled`, so a dangling pool slipped through.
    """
    result: list[Issue] = []
    sec = rbd.sections.get("BLOCK_DATA")
    if not sec:
        return result

    def names(section: str, column: str) -> set[str]:
        rows = rbd.sections.get(section)
        return {r.get(column, "") for r in (rows.rows if rows else [])}

    pool_names = names("SPARE_POOL_DATA", "PoolName")
    resource_names = names("RESOURCE_DATA", "PoolName")
    trigger_names = names("TRIGGERS", "TriggerName")

    for row in sec.rows:
        try:
            bid = int(float(row.get("ID", "0")))
        except (ValueError, TypeError):
            continue
        if bid == 0:
            continue
        name = row.get("blockName") or f"Block {bid}"
        if str(row.get("SpType", "")).strip().lower() == "pooled":
            pool = row.get("SparePoolName", "")
            if pool not in pool_names:
                result.append(Issue(
                    "error", f"Block {bid} '{name}'",
                    f"uses spare pool '{pool}', which is not defined in the "
                    f"spare pools table"))
        resource = str(row.get("Resource1Name", "")).strip()
        if resource not in ("", "None") and resource not in resource_names:
            result.append(Issue(
                "error", f"Block {bid} '{name}'",
                f"uses resource '{resource}', which is not defined in the "
                f"resources table"))
        trigger = str(row.get("pmTrig", "")).strip()
        if trigger not in ("", "None", "none") and trigger not in trigger_names:
            result.append(Issue(
                "error", f"Block {bid} '{name}'",
                f"uses trigger '{trigger}', which is not defined in the "
                f"triggers table"))
    return result


def _check_gd_types(rbd: RbdFile) -> list[Issue]:
    """A GDType other than 0-3 (1 linear, 2 geometric, 3 asymptotic).

    Raptor 7 reads and runs such a file (7 golden fixtures have blocks with
    GDType 4, three of them captured: `GDTypeProbe`, `LadL7Gd`, the ZeroFail
    probes). In the original the growth/decay stress is then left uninitialised;
    the engine reproduces the observed result by taking 0 and clamping it to
    GDLimit / mean failure time, which is only deterministic when that ratio is
    inside (0, 1). So: a WARNING when the engine can reproduce it, an ERROR (the
    same refusal `combined_stress` would raise mid-run) when the ratio is outside
    that window, where Raptor 7's value depends on stack garbage."""
    result: list[Issue] = []
    sec = rbd.sections.get("BLOCK_DATA")
    if not sec:
        return result

    model = None
    for row in sec.rows:
        try:
            bid = int(float(row.get("ID", "0")))
        except (ValueError, TypeError):
            continue
        if bid == 0:
            continue
        try:
            gd_type = int(float(row.get("GDType", "0")))
        except (ValueError, TypeError):
            gd_type = 0
        if gd_type in (0, 1, 2, 3):
            continue
        name = row.get("blockName") or f"Block {bid}"
        ratio = None
        try:
            if model is None:
                from raptor.core.engine.load import load_model
                model = load_model(rbd)
            from raptor.core.engine.block_lifecycle import fail_mean
            block = model.blocks_by_id[bid]
            ratio = block.gd_limit / fail_mean(block)
        except Exception:  # noqa: BLE001 - cannot show it is deterministic
            ratio = None
        if ratio is not None and 0.0 < ratio < 1.0:
            result.append(Issue(
                "warning", f"Block {bid} '{name}'",
                f"GDType {gd_type} is not a growth/decay type (1 linear, 2 geometric, "
                f"3 asymptotic); Raptor leaves the growth stress undefined for it. "
                f"The simulation uses the fixed point GDLimit / mean time to failure "
                f"({ratio:.4g}), as the captured runs do"))
        else:
            result.append(Issue(
                "error", f"Block {bid} '{name}'",
                f"GDType {gd_type} is not a growth/decay type (1 linear, 2 geometric, "
                f"3 asymptotic) and GDLimit / mean time to failure is "
                f"{'not computable' if ratio is None else format(ratio, '.4g')}, outside "
                f"(0, 1): Raptor's result depends on uninitialised memory and cannot "
                f"be reproduced. Use GDType 0-3"))
    return result


def _check_fail_mean_undefined(rbd: RbdFile, result: list[Issue]) -> None:
    """Undefined-mean failure distributions on a block whose dependency
    can send it to PM.

    The dependency's percentage-based PM trigger uses the block's own
    failure mean (``_obpm_over_threshold`` -> ``dependency.fail_mean``),
    so a distribution whose mean does not exist has no threshold to
    compare against.  Only Weibull shape < 0.05, Pearson5 shape <= 1.0
    and Pearson6 shape <= 1.0 have a genuinely undefined mean (the engine
    substitutes the literal ``12345.6789`` sentinel for them, exactly as
    Raptor 7 does).  The Pearson6 shape is the SECOND parameter, the
    one `SetStats` guards on and `distro_mean` divides by.

    The gate is ``DepPM > 0`` -- the *dependency's* percentage sent to PM
    -- **not** the block's ``PMFreq``.  ``PMFreq`` is a maintenance
    interval (and defaults to 100 on every block), so gating on it
    wrongly rejected any dependent block that had never been given PM.
    Every other distribution (including Exponential) has a ported mean
    and is accepted.
    """
    sec = rbd.sections.get("BLOCK_DATA")
    if not sec:
        return

    from raptor.core.rng import Distribution

    for row in sec.rows:
        try:
            bid = int(float(row.get("ID", "0")))
        except (ValueError, TypeError):
            continue
        if bid == 0:
            continue

        dep_type = row.get("DepType", "None")
        try:
            dep_pm = float(row.get("DepPM", "0") or "0")
        except (ValueError, TypeError):
            dep_pm = 0.0

        # Only the dependency's own PM outcome needs the fail mean.
        if dep_type in (None, "", "None") or dep_pm <= 0:
            continue

        try:
            fail_distro = int(float(row.get("FDist", "0")))
            fail_values = [float(row.get(f"FParam{i}", "0")) for i in (1, 2, 3)]
        except (ValueError, TypeError):
            continue

        name = row.get("blockName") or f"Block {bid}"
        element = f"Block {bid} '{name}'"
        # NB: gate on DepPM, which the percentage-based PM trigger uses --
        # see this function's docstring.
        if fail_distro == int(Distribution.WEIBULL) and fail_values[0] < 0.05:
            result.append(Issue(
                "error", element,
                f"Weibull shape {fail_values[0]} < 0.05 - the failure mean is "
                f"undefined, but the block's dependency sends {dep_pm:g}% "
                f"of outages to PM, which needs that mean as its threshold; "
                f"set dep_pm: 0 or use a distribution with a defined mean",
            ))
        elif fail_distro == int(Distribution.PEARSON5) and fail_values[0] <= 1.0:
            result.append(Issue(
                "error", element,
                f"Pearson5 shape {fail_values[0]} <= 1.0 - the failure mean is "
                f"undefined, but the block's dependency sends {dep_pm:g}% "
                f"of outages to PM, which needs that mean as its threshold; "
                f"set dep_pm: 0 or use a distribution with a defined mean",
            ))
        elif fail_distro == int(Distribution.PEARSON6) and fail_values[1] <= 1.0:
            # Pearson6's out-of-range test is on the SECOND parameter, not
            # the first: `SetStats` WHEN 12 reads `valsInUse[2]` for the
            # shape guard and divides by `(valsInUse[2]-1.)`
            # which is what `distro_mean` does
            # too. Only Pearson5 and Weibull guard on param 1.
            result.append(Issue(
                "error", element,
                f"Pearson6 shape {fail_values[1]} <= 1.0 - the failure mean is "
                f"undefined, but the block's dependency sends {dep_pm:g}% "
                f"of outages to PM, which needs that mean as its threshold; "
                f"set dep_pm: 0 or use a distribution with a defined mean",
            ))


def _check_unrunnable_model(rbd: RbdFile) -> list[Issue]:
    """ERROR for a model the engine refuses to run (a cold-standby path that
    depends on another path into the same node, which crashes Raptor 7),
    and a WARNING for each dependency cycle (legal -- Raptor 7 runs them -- but
    worth a look). The rules live in ``raptor/core/engine/model_checks.py`` and
    are the same ones ``load_model`` raises ``UnrunnableModel`` from, so the
    pre-flight message and the refusal at Simulate cannot disagree.

    Anything else that stops the model loading is not this check's business
    (other rules report it), so it is ignored here."""
    from raptor.core.engine.errors import UnrunnableModel
    from raptor.core.engine.load import load_model
    from raptor.core.engine.model_checks import dependency_cycle_warnings

    try:
        model = load_model(rbd)
    except UnrunnableModel as exc:
        return [Issue("error", problem.element, problem.message) for problem in exc.problems]
    except Exception:  # noqa: BLE001 - other load failures are reported elsewhere
        return []
    return [Issue("warning", problem.element, problem.message)
            for problem in dependency_cycle_warnings(model.blocks_by_id, model.nodes_by_id)]


_NAMED_BY_REFERENCE = (
    ("SPARE_POOL_DATA", "PoolName", "spare pool", "blocks name their spare pool"),
    ("RESOURCE_DATA", "PoolName", "resource pool", "blocks name their resource pool"),
    ("TRIGGERS", "TriggerName", "trigger", "blocks name the trigger that starts their preventive maintenance"),
    ("PHASES", "PhaseName", "phase", "tables and tools find a phase by its name"),
)


def _check_duplicate_names(rbd: RbdFile) -> list[Issue]:
    """WARNING for two pools, triggers or phases sharing a name. Other things refer to them BY NAME, so the second
    is unreachable or ambiguous (Raptor 7's own dialogs offer to update the existing one rather than add another).
    Blocks, nodes and events may share names, as Raptor 7's own examples do."""
    out: list[Issue] = []
    for section, key, noun, why in _NAMED_BY_REFERENCE:
        sec = rbd.sections.get(section)
        if sec is None:
            continue
        seen: set[str] = set()
        reported: set[str] = set()
        for row in sec.rows:
            if row.get("ID") == "0":
                continue
            name = row.get(key, "")
            if not name:
                continue
            if name in seen and name not in reported:
                reported.add(name)
                out.append(Issue("warning", name, f"two {noun}s are named {name!r}: {why}, so only one of them "
                                                  f"can be used; rename one"))
            seen.add(name)
    return out


def _check_dependency_targets(rbd: RbdFile) -> list[Issue]:
    """ERROR when a dependency names an unknown kind or missing element."""
    ids = {}
    for sec in ("BLOCK_DATA", "NODE_DATA", "EVENT_DATA", "HIERARCHY_DATA"):
        section = rbd.sections.get(sec)
        ids[sec] = {str(r.get("ID")) for r in section.rows} if section else set()
    kind_sec = {"RBDBlock": "BLOCK_DATA", "RBDNode": "NODE_DATA",
                "RBDEvent": "EVENT_DATA", "RBDHier": "HIERARCHY_DATA"}
    out: list[Issue] = []
    for sec, kind, name_col, type_col, num_col in (
            ("BLOCK_DATA", "Block", "blockName", "DepType", "DepNum"),
            ("NODE_DATA", "Node", "Name", "depType", "depNum")):
        s = rbd.sections.get(sec)
        if not s:
            continue
        for row in s.rows:
            dependency_type = str(row.get(type_col, "None") or "None")
            try:
                dependency_num = int(float(row.get(num_col, "0") or 0))
            except (TypeError, ValueError):
                dependency_num = 0
            if dependency_num <= 0:
                continue
            target_sec = kind_sec.get(dependency_type)
            name = row.get(name_col) or f"{kind} {row.get('ID')}"
            if target_sec is None:
                out.append(Issue(
                    "error", f"{kind} '{name}'",
                    f"dependency type {dependency_type!r} is not one of "
                    f"{', '.join(sorted(kind_sec))}"))
            elif str(dependency_num) not in ids[target_sec]:
                out.append(Issue(
                    "error", f"{kind} '{name}'",
                    f"dependency target {dependency_type} {dependency_num} does not exist"))
    return out


def _check_node_k_over_n(rbd: RbdFile) -> list[Issue]:
    """ERROR when a node's required-good count ``k`` exceeds its real
    incoming path count.

    A node's ``n`` comes from its incoming links, so a ``k=2`` node with
    only one incoming link is a "2 of 1" vote: trivially satisfied, the
    node can never vote the system down.  Raptor 7 accepts the model
    (build reports ``runnable: true``) and the run silently returns a
    plausible wrong number -- e.g. a 2-of-3 that was wired with the blocks
    hanging *off* the node instead of feeding *into* it.  The fix is
    always to wire the redundant blocks into the voting node.
    """
    node_sec = rbd.sections.get("NODE_DATA")
    if not node_sec:
        return []
    link_sec = rbd.sections.get("LINK_DATA")
    real_in: dict[str, int] = {}
    for link in (link_sec.rows if link_sec else []):
        if str(link.get("ToType", "")).replace("RBD", "", 1).lower() == "node":
            key = str(link.get("ToID", ""))
            real_in[key] = real_in.get(key, 0) + 1

    result: list[Issue] = []
    for row in node_sec.rows:
        elem_id = str(row.get("ID", "0"))
        if elem_id == "0":
            continue
        k = _int_or(row.get("k"))
        if k is None or k <= 0:
            continue          # -1 is the "never edited" sentinel (-> 1)
        n = real_in.get(elem_id, 0)
        if n == 0:
            continue          # unconnected node: core.input_rules reports it
        if k > n:
            name = row.get("Name") or f"Node {elem_id}"
            result.append(Issue(
                "error", f"Node {elem_id} '{name}'",
                f"k={k} exceeds the node's {n} incoming path(s) -- this is a "
                f"\"{k} of {n}\" vote that can never fail. Wire the redundant "
                f"blocks INTO this node (a node's n comes from its incoming "
                f"links), or lower k to at most {n}",
            ))
    return result


# ---------------------------------------------------------------------------
# Topology: block/event fan-in and fan-out
# ---------------------------------------------------------------------------

def _int_or(value) -> int | None:
    """Legacy rows store ints as strings; return a clean int or None."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _truthy(value) -> bool:
    """The legacy boolean convention: ``1``/``T``/``TRUE`` are true."""
    return str(value).strip().upper() in ("1", "T", "TRUE", "YES")


def _raptor7_input_rules(rbd: RbdFile) -> list[Issue]:
    """Every rule of `raptor.core.input_rules` the model breaks, as issues: a
    rule enforced before a run is an error; one enforced when a link is
    drawn or a value typed is a warning. The text is the tool's own message.
    Source locations (and friends) are developer detail:
    they stay in the rule definitions and are never shown to a user.
    """
    from raptor.core.input_rules import check
    return [Issue(v.severity, v.element, v.message) for v in check(rbd)]


# ---------------------------------------------------------------------------
# Capacity settings
# ---------------------------------------------------------------------------

def _link_has_real_cap(row: dict) -> bool:
    """True when a link stores a real ``nomFlow``/``MaxFlow`` cap.

    The columns use sentinels: ``-1`` means "flowGenerated" and ``-2``
    means "even share of flow" (see ``engine/capacity.py``), so only
    values ``>= 0`` are user-set caps.
    """
    for col in ("nomFlow", "MaxFlow"):
        v = _int_or(row.get(col))
        if v is not None and v >= 0:
            return True
    return False


def _check_start_to_end_reachable(rbd: RbdFile) -> list[Issue]:
    """WARN when no End marker is reachable from any Start marker.

    This check used to exist ONLY inside the GUI window
    (`main_window.py`'s `_validation_issues`), wrapped in
    `except Exception: pass`. A capability trapped in a widget is a
    front-door asymmetry: a model with broken hierarchy wiring was caught
    in the GUI and silently accepted through the API.

    It is an ERROR-shaped problem but a WARNING here, matching the GUI,
    which reported it without blocking Run. Blocks/Events can legitimately
    be unreachable in a partial model, so making it blocking would refuse
    models that simulate fine.

    Reads the model's own rows (LINK_DATA, NODE_DATA `Type`) rather than
    the engine's loaded graph. That is deliberate: it needs no Qt, no
    engine import, and no `load_model` call that could raise -- so it runs
    on any model, including one the engine cannot yet load.
    """
    result: list[Issue] = []
    node_sec = rbd.sections.get("NODE_DATA")
    link_sec = rbd.sections.get("LINK_DATA")
    if not node_sec or not link_sec:
        return result

    def _kind(ref: Any) -> str:
        return str(ref or "").replace("RBD", "", 1)

    adjacency: dict[tuple[str, Any], list[tuple[str, Any]]] = {}
    for row in link_sec.rows:
        src = (_kind(row.get("FromType")), row.get("FromID"))
        dst = (_kind(row.get("ToType")), row.get("ToID"))
        adjacency.setdefault(src, []).append(dst)

    starts: list[tuple[str, Any]] = []
    ends: set[tuple[str, Any]] = set()
    for row in node_sec.rows:
        try:
            node_type = int(float(row.get("Type", "0")))
        except (TypeError, ValueError):
            continue
        key = ("Node", row.get("ID"))
        if node_type == 1:
            starts.append(key)
        elif node_type == 3:
            ends.add(key)

    if not starts:
        return result          # nothing to walk from; not this check's business

    seen: set[tuple[str, Any]] = set()
    stack = list(starts)
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        stack.extend(d for d in adjacency.get(key, []) if d not in seen)

    if not any(end in seen for end in ends):
        result.append(Issue(
            "warning", "model",
            "No path from Start to End -- the model's flow is broken "
            "(check hierarchy wiring and links)"))
    return result


def _check_capacity_settings(rbd: RbdFile) -> list[Issue]:
    """WARN when capacity analysis is enabled but cannot bind.

    Capacity caps on links only take effect when the flow passes into a
    capacity-boundary node -- one with ``fullflow=0`` ("Unrestricted
    flow" unchecked) or ``divFlow=0`` ("Use any available path"
    unchecked); see ``engine/capacity.py``'s ``build_capacity_graph``.
    A model with ``capacityAnalysis`` on but no such node, or with no
    demand (``flowGenerated <= 0``), silently returns a wrong answer.
    """
    result: list[Issue] = []
    sys_sec = rbd.sections.get("SYSTEM_DATA")
    if not sys_sec or not sys_sec.rows:
        return result
    sys_row = sys_sec.rows[0]
    if not _truthy(sys_row.get("capacityAnalysis")):
        return result

    flow_generated = _int_or(sys_row.get("flowGenerated")) or 0
    if flow_generated <= 0:
        result.append(Issue(
            "warning", "System",
            "capacityAnalysis is on but flowGenerated <= 0 -- demand is "
            "zero, so no flow is generated and the capacity analysis "
            "cannot report anything; set flow_generated to the demand "
            "entering the Start node",
        ))

    node_sec = rbd.sections.get("NODE_DATA")
    nodes = node_sec.rows if node_sec else []
    boundary = [
        n for n in nodes
        if _int_or(n.get("fullflow")) == 0
        or _int_or(n.get("divFlow")) == 0
    ]
    for marker in boundary:
        kind = {"4": "In", "5": "Out"}.get(str(_int_or(marker.get("Type"))))
        if kind is None:
            continue
        label = marker.get("Name") or marker.get("ID")
        result.append(Issue(
            "warning", f"Node {marker.get('ID')} '{label}'",
            f"this hierarchy {kind} marker is set as a capacity boundary "
            "(full_flow=0 or div_flow=0). Raptor 7's own models leave every "
            "marker unrestricted, and with capacity analysis on the caps "
            "then bind on the links into the marker, so flow through the "
            "hierarchy can come out far lower than expected (even zero) "
            "with no error. Put the boundary on an ordinary node instead, "
            "or leave the marker transparent",
        ))
    if not boundary:
        result.append(Issue(
            "warning", "model",
            "capacityAnalysis is on but no node has full_flow=0 or "
            "div_flow=0 -- there is no capacity-boundary node, so every "
            "link's nom_flow/max_flow cap is ignored",
        ))

        link_sec = rbd.sections.get("LINK_DATA")
        links = link_sec.rows if link_sec else []
        capped = [link for link in links if _link_has_real_cap(link)]
        if capped:
            ids = ", ".join(str(link.get("ID")) for link in capped[:5])
            result.append(Issue(
                "warning", "model",
                f"{len(capped)} link(s) carry a real nom_flow/max_flow "
                f"cap (ids {ids}) but no capacity-boundary node exists "
                f"-- those caps are silently ignored; set full_flow=0 or "
                f"div_flow=0 on the node the capped links feed",
            ))

    return result

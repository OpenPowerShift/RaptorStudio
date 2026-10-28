"""Static pre-run validation: what the engine will refuse, found first.

Extracted from `main_window.py`'s `_preflight_issues`, which had grown to
249 lines inside a class of 241 methods. It is a PURE function of the
model -- the only window state it touched was the model, the view (for
element naming) and the undo stack, none of which is a reason for it to
be a method.

Keeping it a function is the point. The review's finding about "why is
Run greyed out?" is a UI problem, and a UI problem cannot be fixed
while the answer lives inside the widget that has the question.

Deliberately conservative: only UNCONDITIONAL engine gates are mirrored
here, and each by DELEGATING to `raptor.api.validate` (one rule set; the
engine decides). Pooled spares and PM-with-resources simulate fine, so
they are not gated.
"""
from __future__ import annotations

from typing import Any


def preflight_issues(rbd, view, elem_name, api_issues=None) -> list[str]:
    """Statically flag model features the engine will reject with
    NotImplementedError at runtime -- before the user clicks Run.
    Deliberately conservative: only unconditional gates are mirrored
    here, by delegation to `api.validate`.

    `api_issues` is an optional already-computed `api.validate.issues(rbd)`
    result. When it is supplied, the checks that merely DELEGATE to that
    validator are skipped and the caller already has their answers. The
    GUI used to run the validator twice per validation pass (once here,
    once for its own error list), and on a 3,500-block model each pass
    rebuilds the engine graph -- two copies of one answer, seconds apart.
    The view-based rules below are NOT in `api.validate`, so they still
    run.
    """
    issues: list[str] = []
    if rbd is None:
        return issues
    sec = rbd.sections.get("BLOCK_DATA")
    if not sec:
        return issues
    if api_issues is None:
        # SimStart and GDType are DELEGATED. This used to restate both checks
        # against the parsed block objects, with its own message wording -- and
        # the GDType one was already wrong about what the validator accepts
        # ("only 1, 2, 3 are supported" while `validate._check_gd_types` accepts
        # 0 too). Same duplication class as the fail-mean gate below: two copies
        # of one rule, and the drift is a false positive or a false negative on
        # whether Run is allowed to start.
        from raptor.api.validate import _check_gd_types, _check_sim_start_types
        for issue in _check_sim_start_types(rbd) + _check_gd_types(rbd):
            if issue.severity == "error":
                issues.append(f"{issue.element}: {issue.message}")

        # The dependency's percentage-based PM trigger needs the block's own
        # failure mean (`dependency.fail_mean`), reached only via the OBPM path
        # — dep_type set and DepPM > 0.
        #
        # DELEGATED to the API validator rather than restated here. This used to
        # keep its own allowlist of "distributions whose mean is ported" —
        # Gamma, Weibull, Pearson5 — written when that was all `distro_mean`
        # had, behind an `elif d not in (...)` that flagged every OTHER
        # distribution, and it never picked up the Pearson6 arm the validator
        # gained. It reported `LadL8C`'s S8B2 (Exponential) and S13B3
        # (Lognormal) on a model the engine simulates and that matches real in
        # all 300 runs. Two copies of one rule drift; the validator's is the
        # maintained one and is what an API caller already sees.
        from raptor.api.validate import _check_fail_mean_undefined
        fail_mean_issues: list[Any] = []
        _check_fail_mean_undefined(rbd, fail_mean_issues)
        issues.extend(f"{i.element}: {i.message}" for i in fail_mean_issues)

        # Element connectivity: Raptor 7 refuses to simulate unless
        # every block/event connects from something and to something
        # (its own message pair: "To simulate, block 'X' must connect
        # from something! ... connect to something!"). Mirror both halves
        # separately -- a block with an incoming but no outgoing link
        # would pass an any-link check yet Raptor 7 still rejects it.
        #
        # Needs no view, so it runs before the view-less early return below.
        # Shapes the engine refuses because Raptor 7 crashes on them (a
        # cold-standby path that depends on another path into the same node).
        # The rule is `core.engine.model_checks`'s, via the API validator, so this
        # message and the refusal at Simulate cannot disagree. Errors only:
        # dependency-cycle WARNINGS do not stop a run.
        from raptor.api.validate import _check_unrunnable_model
        from raptor.core.input_rules import check
        issues.extend(f"{i.element}: {i.message}"
                      for i in _check_unrunnable_model(rbd)
                      if i.severity == "error")
        # Raptor 7's own pre-run refusals (`ValidateRBD`/`SendToEngine`), the
        # same rules `validate()` reports as errors, so the GUI, the API and
        # MCP agree on what will not run. Link-shape and dialog rules are
        # warnings there: Raptor 7 runs such a file.
        issues.extend(f"{v.element}: {v.message}"
                      for v in check(rbd) if v.severity == "error")
    # `view` is optional. The rules above read the model's rows and need no
    # view. What follows DOES need
    # the view's parsed topology -- it walks connectivity that no single row
    # records -- so with no view those checks are SKIPPED rather than
    # guessed at: a validator that reports a problem it cannot substantiate
    # is worse than one that stays quiet.
    if view is None:
        return issues
    v = view
    #
    # Dead-end chains: a Block->Block series link whose final block
    # has no outgoing leaves the flow stranded ('no outgoing link
    # found' at Run). Follow each chain and flag the dead end.
    #
    # `out_count` is the per-origin LINKS map this walk needs. It is
    # rebuilt here (it used to be shared with the fan-out check, which is
    # now delegated) because walking a chain needs the link itself, not
    # the key set the membership tests used.
    out_count: dict[tuple[str, int], list[Any]] = {}
    for l in v.links:
        out_count.setdefault(
            (l.connect_f_ref.replace("RBD", "", 1), l.connect_from_id),
            []).append(l)
    for start_id, obj0 in v.blocks.items():
        cur_kind, cur_id, steps = "Block", start_id, 0
        while steps <= len(v.links):
            # `hops`, not `outs`: the map holds LINKS where the old
            # membership-test set held keys. Walking a chain needs the
            # link, and only the first is followed.
            hops = out_count.get((cur_kind, cur_id), [])
            if not hops:
                if cur_kind in ("Block", "Event"):
                    dead_name = elem_name(
                        cur_kind, cur_id)
                    name0 = getattr(obj0, "name", "") or \
                        f"Block {start_id}"
                    issues.append(
                        f"{name0}: its output path leads to "
                        f"'{dead_name}', which has no outgoing "
                        f"link — the flow cannot reach an End")
                break
            nxt = hops[0]
            if nxt.connect_t_ref.endswith("Node"):
                break   # reached a node: fine
            cur_kind = nxt.connect_t_ref.replace("RBD", "", 1)
            cur_id = nxt.connect_to_id
            steps += 1

    # Cyclic graphs: the engine's TestFail walk raises
    # "TestFail loop detected" mid-run (real MODSIM crashes itself --
    # LoopError). Any directed cycle among links is suspect; flag
    # cycles reachable from a block/event start (node-only rings
    # behind hierarchy are not exercised by any fixture).
    adj: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for l in v.links:
        adj.setdefault((l.connect_f_ref, l.connect_from_id), []).append(
            (l.connect_t_ref, l.connect_to_id))
    state: dict[tuple[str, int], int] = {}  # 1=on stack, 2=done

    def _dfs(k, path):
        if state.get(k) == 1:
            cycle = [p for p in path[path.index(k):]] if k in path else [k]
            names = []
            for ck, cid in cycle[:5]:
                pool = (v.nodes if ck.endswith("Node")
                        else v.events if ck.endswith("Event") else v.blocks)
                o = pool.get(cid)
                names.append(getattr(o, "name", None)
                             or getattr(o, "node_name", None)
                             or f"{ck} {cid}")
            issues.append(
                "cyclic connection: " + " -> ".join(names)
                + " — a RBD graph must be acyclic (the simulation would abort "
                  "with a LoopError)")
            return True
        if state.get(k) == 2:
            return False
        state[k] = 1
        for nxt in adj.get(k, ()):
            if _dfs(nxt, path + [k]):
                state[k] = 2
                return True
        state[k] = 2
        return False

    for kind, pool in (("Block", v.blocks), ("Event", v.events)):
        for oid in pool:
            if oid and _dfs((f"RBD{kind}", oid), []):
                break

    # In-node targets: a block/event wired into a hierarchy In node
    # (type 4) hits the engine's block/In-node fix-up guard mid-run
    # (real MODSIM crashes itself on these). Flag the direct case.
    for l in v.links:
        if not l.connect_t_ref.endswith("Node"):
            continue
        node = v.nodes.get(l.connect_to_id)
        if node is None or getattr(node, "type_node", 0) != 4:
            continue
        src_pool = (v.blocks if l.connect_f_ref.endswith("Block")
                    else v.events if l.connect_f_ref.endswith("Event")
                    else None)
        src = src_pool.get(l.connect_from_id) if src_pool else None
        name = (getattr(src, "name", None)
                or f"{l.connect_f_ref} {l.connect_from_id}")
        issues.append(
            f"{name}: connects into hierarchy In node "
            f"'{getattr(node, 'node_name', None) or l.connect_to_id}' "
            "— wire it to an Out node instead (the simulation cannot run "
            "In-node block/event connections)")

    # CycleSim without phases: Raptor 7 refuses the model up front
    # ("You cannot use cycle terminated without phases defined") and
    # the engine mirrors that rejection at run time -- flag it in
    # preflight so the user sees it before pressing Run.
    if rbd is not None and "SYSTEM_DATA" in rbd.sections \
            and rbd.sections["SYSTEM_DATA"].rows:
        try:
            from raptor.core.fileio.typed import system_config_from_row
            sys_cfg = system_config_from_row(
                rbd.sections["SYSTEM_DATA"].rows[0])
            phases = rbd.sections.get("PHASES")
            if sys_cfg.termination_type == "CycleSim" and \
                    (phases is None or not phases.rows):
                issues.append(
                    "CycleSim termination without any phases defined — "
                    "this model cannot run ('You cannot use "
                    "cycle terminated without phases defined')")
        except (ValueError, KeyError, IndexError):
            pass

    # Phases/Triggers distribution parameters: unlike Blocks'
    # Failure/Repair/PM params (validated on commit via
    # `validate_distro_params` in both the Inspector and Data
    # Tables), the Phases/Triggers Data Tables grid is a generic
    # "keyed" table with no per-distribution validation at all --
    # a 0.0 Pearson6 scale (or any other "must be positive" param)
    # saves silently and only blows up at simulation time deep in
    # rng.py with a bare `math.log` ValueError and no indication of
    # which phase/trigger caused it. Catch it here instead, named.
    # Skipped when the caller supplied `api_issues`: the API validator's
    # `_check_section_dist_params` is the same rule over the same rows.
    if api_issues is None:
        from raptor.core.distro_rules import validate_distro_params
        for section_name, name_col, label in (
                ("PHASES", "PhaseName", "Phase"),
                ("TRIGGERS", "TriggerName", "Trigger")):
            sec = rbd.sections.get(section_name) if rbd else None
            for row in (sec.rows if sec else []):
                try:
                    dist = int(float(row.get("Dist", "0")))
                    params = [float(row.get(f"Param{i}", "0"))
                              for i in (1, 2, 3)]
                except (ValueError, TypeError):
                    continue
                err = validate_distro_params(dist, params, label)
                if err:
                    row_name = row.get(name_col) or f"{label} (unnamed)"
                    issues.append(f"{label} '{row_name}': {err}")
    return issues


def preflight_warnings(rbd) -> list[str]:
    """Non-blocking model warnings, for the Run panel's readiness line.

    Delegated to `api.validate` (the one rule set): every `warning`-severity
    issue it reports, as "<element>: <message>". Dependency cycles are the
    main case -- Raptor 7 runs them (five captures, `DepCycle*Probe` and
    `DepSelfProbe`), so they warn and never block.
    """
    if rbd is None:
        return []
    from raptor.api.validate import issues

    def user_text(msg: str) -> str:
        """Drop the developer source citation from a user-facing warning.

        The API/MCP no longer append the source location either (the same
        scrub applies to every user-facing surface); the Issues list must not quote source code at a modeller.
        """
        return msg.split(" [RAPTOR Studio, ", 1)[0].rstrip()

    return [f"{i.element}: {user_text(i.message)}" for i in issues(rbd)
            if i.severity == "warning"]

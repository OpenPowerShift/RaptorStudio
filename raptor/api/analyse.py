"""Analyses API — single point failures, failure effects, weak link,
comparison and parameter sweep.

Wraps engine functions into MCP-friendly calls.  No Qt imports.
"""
from __future__ import annotations

import copy
import functools
import itertools
import threading
from typing import Any

from raptor.api.run import (run, run_dir, RunResult, RunNotFoundError,
                            RunCancelledError)
from raptor.api.results import Metric
from raptor.api.workspace import Workspace, UnknownHandle


class AnalysisError(Exception):
    """Base error for analysis operations."""


#: Serialises `sweep`/`compare`, which each create/close several scratch
#: models in the shared workspace.  Two issued concurrently (an assistant
#: naturally reaches for Promise.all) used to race on model-id allocation
#: and eviction, surfacing as an expired handle or a missing model
#: directory.  A single process-wide lock makes them safe at the cost of
#: running one at a time -- the right trade for what-if studies.
_ANALYSIS_LOCK = threading.RLock()


def _serialised(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _ANALYSIS_LOCK:
            return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Single Point Failures
# ---------------------------------------------------------------------------

def single_point_failures(workspace: Workspace, model_id: str, *,
                          workers: int = 1,
                          use_cache: bool = True) -> list[dict]:
    """The Single Point Failure rows, found as Raptor 7 does: each Running or
    Standby block is failed in Failure Effects View and the system tested
    (`raptor.core.engine.spf_fev`), so dependencies and cold standby count.

    Each row: ``{"kind": "Block"|"Event", "id": int, "name": str, "crit": float}``;
    a phased model's rows also carry ``"phase"`` (one set per phase).

    The result is content-addressed by the model hash, so asking again after
    no edit returns without re-testing 3,000+ candidates.  `workers > 1`
    parallelises the independent candidate tests on an unphased model.
    """
    import json

    from raptor.api.run import model_hash as _hash
    handle = workspace.get(model_id)
    digest = _hash(handle.rbd)
    cache_dir = workspace.root / ".spf_cache"
    cache_path = cache_dir / f"{digest}.json"
    if use_cache:
        try:
            cached = json.loads(cache_path.read_text())
            if isinstance(cached, list):
                return cached
        except (OSError, ValueError):
            pass
    from raptor.core.engine.spf_fev import single_point_failures as _spf
    rows = _spf(handle.rbd, workers=workers)
    if use_cache:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(rows))
        except OSError:
            pass
    return rows


def _criticality_on(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    from raptor.core.dev_options import from_environment
    return from_environment().show_criticality


def spf_listing(workspace: Workspace, model_id: str,
                file_name: str | None = None, *, criticality: bool | None = None) -> str:
    """The Raptor 7-format Single Point Failure file text (CRLF), as the
    original's `Create Single Point Failure File` writes it. Phased models
    get the per-phase layout. `criticality=None` follows the `show_criticality` developer
    option (`RAPTOR_DEV_FEATURES`)."""
    handle = workspace.get(model_id)
    criticality = _criticality_on(criticality)
    from raptor.core.engine.spf import spf_date_string
    from raptor.core.engine.spf_fev import build_spf_listing
    return build_spf_listing(handle.rbd, file_name or f"{handle.name}.txt",
                             spf_date_string(), password=criticality)


# ---------------------------------------------------------------------------
# Failure Effects
# ---------------------------------------------------------------------------

def _resolve_fev_element(workspace: Workspace, model_id: str, selector: str | int) -> tuple[str, int]:
    """A block or event selector -> ("block"|"event", id)."""
    if isinstance(selector, int):
        return "block", selector
    if isinstance(selector, str) and selector.strip().lstrip("-").isdigit():
        return "block", int(selector)
    from raptor.api.build import BuildError, resolve_element
    try:
        section, row = resolve_element(workspace.get(model_id).rbd, str(selector))
    except BuildError as exc:
        raise AnalysisError(str(exc)) from exc
    if section == "BLOCK_DATA":
        return "block", int(row["ID"])
    if section == "EVENT_DATA":
        return "event", int(row["ID"])
    raise AnalysisError(
        f"{selector!r} is not a block or an event; Failure Effects View "
        "changes blocks and events only")


def failure_effects(
    workspace: Workspace,
    model_id: str,
    *,
    fail: list[str | int] | None = None,
    repair: list[str | int] | None = None,
    phase: int | None = None,
) -> dict:
    """Failure Effects View as a script: start from the all-operating state
    (phase 1 of a phased model), optionally go to `phase`, fail the `fail`
    elements in order, then repair the `repair` elements.

    Runs `raptor.core.engine.fev.FevSession`, the port of Raptor 7's Failure
    Effects View: nothing is simulated and no random number is drawn. Returns
    ``{"system_status", "affected", "log", "effects", "criticality", "phase"}``;
    `log` is the Failure Effects text (what Raptor 7's Write To Failure
    Effects Log appends), `effects` the same facts as lists.
    """
    from raptor.core.engine.errors import UnrunnableModel
    from raptor.core.engine.fev import FevSession

    handle = workspace.get(model_id)
    try:
        session = FevSession(handle.rbd)
    except UnrunnableModel as exc:
        raise AnalysisError(str(exc)) from exc
    return _fev_scenario(workspace, model_id, session, fail or [], repair or [],
                         phase)


def capacity_lines(capacity: dict) -> list[str]:
    """A failure-effects `capacity` result as text (the CAPACITY section the
    GUI shows; `raptor.core.engine.fmeca.capacity_lines`)."""
    from raptor.core.engine.fmeca import capacity_lines as _lines
    return _lines(capacity)


def _fev_scenario(workspace: Workspace, model_id: str, session, fail, repair,
                  phase: int | None) -> dict:
    """One failure-effects scenario on a session that is in its start state."""
    from raptor.core.engine.fmeca import build_fmeca, fmeca_lists
    from raptor.core.model import BlockStatus, EventStatus

    if phase is not None:
        if not session.phased:
            raise AnalysisError("this model has no phases")
        n = len(session.model.phases)
        if not 1 <= phase <= n:
            raise AnalysisError(f"phase {phase} is not in 1..{n}")
        session.goto_phase(phase)

    affected: list[str] = []
    for selector in fail:
        kind, eid = _resolve_fev_element(workspace, model_id, selector)
        if kind == "event":
            event = session.model.events_by_id.get(eid)
            if event is None or event.op_status != EventStatus.SUCCESS:
                raise AnalysisError(
                    f"Could not fail event {selector!r} (already failed or not found)")
            session.click_event(eid)
        else:
            b = session.model.blocks_by_id.get(eid)
            if b is None or not (b.op_status == BlockStatus.RUNNING or (
                    b.op_status == BlockStatus.STANDBY and b.sb_stress > 0.0)):
                raise AnalysisError(
                    f"Could not fail block {selector!r} "
                    "(already failed, not operating, or not found)")
            session.click_block(eid)
        affected.append(str(selector))
    for selector in repair:
        kind, eid = _resolve_fev_element(workspace, model_id, selector)
        if kind == "event":
            event = session.model.events_by_id.get(eid)
            if event is None or event.op_status != EventStatus.FAILURE:
                raise AnalysisError(
                    f"Could not repair event {selector!r} (not failed or not found)")
            session.click_event(eid)
        else:
            b = session.model.blocks_by_id.get(eid)
            if b is None or b.op_status not in (BlockStatus.REPAIRING, BlockStatus.PM):
                raise AnalysisError(
                    f"Could not repair block {selector!r} (already running or not found)")
            session.click_block(eid)
        affected.append(str(selector))

    result = {
        "system_status": session.system_color.value,
        "affected": affected,
        "log": build_fmeca(session, show_criticality=_criticality_on(None)),
        "effects": fmeca_lists(session),
        "criticality": session.criticality,
        "phase": session.phase_number if session.phased else None,
    }
    capacity = session.capacity()
    if capacity is not None:
        result["capacity"] = capacity
    return result



#: Most scenarios one `failure_effects_many` call takes.
MAX_FEV_SCENARIOS = 2000


def failure_effects_many(workspace: Workspace, model_id: str,
                         scenarios: list[dict], *,
                         include_log: bool = False) -> list[dict]:
    """Many independent failure-effects scenarios on ONE model set-up.

    Each scenario is `{"fail": [...], "repair": [...], "phase": N}` (every key
    optional) and starts from the all-operating state, exactly as a separate
    `failure_effects` call does; the model is set up once instead of once per
    scenario, which is what makes hundreds of combinations on a large model
    practical. Returns one dict per scenario, in order: the
    `failure_effects` result (without the `log` text unless `include_log`),
    plus the scenario's `fail`/`repair`. A scenario that cannot be applied
    (an unknown or already-failed element, a bad phase) gets `{"error": ...}`
    in place of its result and the rest still run."""
    from raptor.core.engine.errors import UnrunnableModel
    from raptor.core.engine.fev import FevSession

    if len(scenarios) > MAX_FEV_SCENARIOS:
        raise AnalysisError(
            f"{len(scenarios)} scenarios is over the limit of {MAX_FEV_SCENARIOS} "
            "per call; split the list")
    try:
        session = FevSession(workspace.get(model_id).rbd)
    except UnrunnableModel as exc:
        raise AnalysisError(str(exc)) from exc
    out: list[dict] = []
    for i, scenario in enumerate(scenarios):
        fail = list(scenario.get("fail") or [])
        repair = list(scenario.get("repair") or [])
        session.refresh_all()
        row: dict = {"index": i, "fail": fail, "repair": repair}
        try:
            result = _fev_scenario(workspace, model_id, session, fail, repair,
                                   scenario.get("phase"))
        except AnalysisError as exc:
            row["error"] = str(exc)
        else:
            if not include_log:
                result.pop("log")
            row.update(result)
        out.append(row)
    return out


FEV_SCRIPT_ACTIONS = {
    "click": "left-click a block or event (cycles a hatched block)",
    "fail": "click an operating block, or an event that has not failed",
    "repair": "click a failed or PM block, or a failed event",
    "phase": "go to phase N",
    "next": "the forward phase arrow (wraps)",
    "prev": "the back phase arrow (wraps)",
    "erase": "the large eraser: everything operating, phase 1",
    "erase-phase": "the small eraser: clear failures, keep the phase",
    "write": "Write To Failure Effects Log: record the text now",
}


def parse_fev_script(text: str) -> list[tuple[str, str]]:
    """One action per line: ``ACTION [ARGUMENT]``; blank lines and ``#`` comments
    are ignored. Raises `AnalysisError` naming the line."""
    actions: list[tuple[str, str]] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        verb, _, arg = line.partition(" ")
        verb, arg = verb.lower(), arg.strip()
        if verb not in FEV_SCRIPT_ACTIONS:
            raise AnalysisError(
                f"line {n}: unknown action {verb!r}; use one of "
                f"{', '.join(FEV_SCRIPT_ACTIONS)}")
        needs_arg = verb in ("click", "fail", "repair", "phase")
        if needs_arg and not arg:
            raise AnalysisError(f"line {n}: {verb} needs an argument")
        if not needs_arg and arg:
            raise AnalysisError(f"line {n}: {verb} takes no argument")
        actions.append((verb, arg))
    return actions


def fev_script(workspace: Workspace, model_id: str,
               actions: list[tuple[str, str]], *,
               criticality: bool | None = None,
               capacity: bool = False) -> list[dict]:
    """Drive Failure Effects View through `actions` and return one record per
    ``write`` (or a single record of the final state when there is none).
    With `capacity`, a model with capacity analysis adds the delivered flow
    to each record (`FevSession.capacity`)."""
    from raptor.core.engine.errors import UnrunnableModel
    from raptor.core.engine.fev import FevSession
    from raptor.core.engine.fmeca import build_fmeca, fmeca_lists
    from raptor.core.model import BlockStatus, EventStatus

    criticality = _criticality_on(criticality)
    try:
        session = FevSession(workspace.get(model_id).rbd)
    except UnrunnableModel as exc:
        raise AnalysisError(str(exc)) from exc

    def record(label: str) -> dict:
        rec = {
            "after": label,
            "system_status": session.system_color.value,
            "phase": session.phase_number if session.phased else None,
            "criticality": session.criticality,
            "lines": build_fmeca(session, show_criticality=criticality),
            "effects": fmeca_lists(session),
        }
        flow = session.capacity() if capacity else None
        if flow is not None:
            rec["capacity"] = flow
        return rec

    def element(arg: str) -> tuple[str, int]:
        return _resolve_fev_element(workspace, model_id, arg)

    def need_phased() -> None:
        if not session.phased:
            raise AnalysisError("this model has no phases")

    writes: list[dict] = []
    last = "start"
    for verb, arg in actions:
        label = f"{verb} {arg}".strip()
        if verb in ("click", "fail", "repair"):
            kind, eid = element(arg)
            if kind == "event":
                event = session.model.events_by_id.get(eid)
                if event is None:
                    raise AnalysisError(f"no event {arg!r}")
                wanted = {"fail": EventStatus.SUCCESS, "repair": EventStatus.FAILURE}.get(verb)
                if wanted is not None and event.op_status != wanted:
                    raise AnalysisError(f"cannot {verb} event {arg!r} (it is {event.op_status.value})")
                session.click_event(eid)
            else:
                b = session.model.blocks_by_id.get(eid)
                if b is None:
                    raise AnalysisError(f"no block {arg!r}")
                if verb == "fail" and not (b.op_status == BlockStatus.RUNNING or (
                        b.op_status == BlockStatus.STANDBY and b.sb_stress > 0.0)):
                    raise AnalysisError(f"cannot fail block {arg!r} (it is {b.op_status.value})")
                if verb == "repair" and b.op_status not in (BlockStatus.REPAIRING, BlockStatus.PM):
                    raise AnalysisError(f"cannot repair block {arg!r} (it is {b.op_status.value})")
                session.click_block(eid)
        elif verb == "phase":
            need_phased()
            try:
                n = int(arg)
            except ValueError:
                raise AnalysisError(f"phase needs a number, got {arg!r}") from None
            if not 1 <= n <= len(session.model.phases):
                raise AnalysisError(f"phase {n} is not in 1..{len(session.model.phases)}")
            session.goto_phase(n)
        elif verb == "next":
            need_phased()
            session.next_phase()
        elif verb == "prev":
            need_phased()
            session.previous_phase()
        elif verb == "erase":
            session.refresh_all()
        elif verb == "erase-phase":
            need_phased()
            session.refresh_phase()
        elif verb == "write":
            writes.append(record(last))
            continue
        last = label
    if not writes:
        writes.append(record(last))
    return writes


# ---------------------------------------------------------------------------
# Weak Link
# ---------------------------------------------------------------------------

def weak_link(workspace: Workspace, run_id: str) -> list[dict]:
    """Extract weak-link rows from a completed run's final results."""
    from raptor.api.run import get_report

    report = get_report(workspace, run_id, section="weak_link", limit=2000)
    rows: list[dict] = []
    for line in report["lines"]:
        stripped = line.strip()
        if not stripped or stripped.startswith("***"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            rows.append({
                "block_name": parts[0],
                "impact": " ".join(parts[1:]),
            })
    return rows


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

_HIGHER_IS_BETTER = {
    "Availability": True,
    "Reliability": True,
    "Conditional_Reliability": True,
    "Green_Percent": True,
    "MDT": False,
    "MMT": False,
    "MTBM": False,
    "MTBDE": True,
    "System_Failures": False,
    "Component_Failures": False,
    "Red_Percent": False,
    "Yellow_Percent": False,
}


def _result_from_run(workspace: Workspace, run_id: str) -> RunResult:
    """Build a RunResult from a cached run's summary.json."""
    import json
    path = run_dir(workspace, run_id) / "summary.json"
    if not path.is_file():
        raise RunNotFoundError(f"Run {run_id!r} not found")
    data = json.loads(path.read_text())
    metrics = [Metric(**v) for v in data.get("metrics", {}).values()]
    return RunResult(
        run_id=run_id,
        model_id=data.get("model_id", run_id),
        num_runs=data.get("num_runs", 0),
        model_name=data.get("model_name", ""),
        metrics=metrics,
        model_hash=data.get("model_hash", ""),
    )


@_serialised
def compare(
    workspace: Workspace,
    models_or_runs: list[str],
    *,
    num_runs: int = 5,
    metrics: list[str] | None = None,
    abort: Any = None,
    progress: Any = None,
) -> dict:
    """Compare two or more models (run afresh) or completed runs (cached).

    Returns per-metric values, deltas against the first entry, and the
    best entry per metric.
    """
    if len(models_or_runs) < 2:
        # Caller-input error (not a model-state problem): ValueError maps to
        # the MCP "invalid_argument" code, unlike AnalysisError.
        raise ValueError("need at least 2 models or runs to compare")
    if num_runs < 1:
        raise ValueError("num_runs must be at least 1")

    results: list[RunResult] = []
    for step, ref in enumerate(models_or_runs):
        if abort is not None and abort.is_set():
            raise RunCancelledError("comparison was cancelled")
        if progress is not None:
            progress(step, len(models_or_runs))
        try:
            workspace.get(ref)
            is_model = True
        except UnknownHandle:
            is_model = False

        if is_model:
            results.append(run(workspace, ref, num_runs=num_runs,
                               timeout_s=120, confirm_long_run=True,
                               abort=abort))
        else:
            results.append(_result_from_run(workspace, ref))

    # Human-readable labels for the raw model_id/run_id refs in
    # `models_or_runs` -- both branches above populate `model_name` (a
    # fresh run always does; a cached run's summary.json carries it from
    # when it was made). Falls back to the raw ref only when a name is
    # genuinely unavailable, and disambiguates duplicate names (two
    # models can share a name) by appending a short ref suffix.
    labels = [r.model_name or ref
              for r, ref in zip(results, models_or_runs)]
    seen: dict[str, int] = {}
    for label in labels:
        seen[label] = seen.get(label, 0) + 1
    for i, label in enumerate(labels):
        if seen[label] > 1:
            # A repeated model/run ref makes the old "(ref)" suffix
            # itself duplicate; include the position to stay unique.
            labels[i] = f"{label} ({models_or_runs[i]}) #{i + 1}"

    names = sorted({m.name for r in results for m in r.metrics})
    if metrics:
        wanted = {m.lower() for m in metrics}
        names = [n for n in names if n.lower() in wanted]

    values: dict[str, list[float | None]] = {}
    for name in names:
        row: list[float | None] = []
        for r in results:
            match = next((m for m in r.metrics if m.name == name), None)
            row.append(match.mean if match else None)
        values[name] = row

    deltas: dict[str, float | None] = {}
    for name in names:
        base = values[name][0]
        for i, label in enumerate(labels[1:], start=1):
            other = values[name][i]
            if base is not None and other is not None:
                deltas[f"{name}_delta_{label}"] = other - base

    best: dict[str, str] = {}
    for name in names:
        row = values[name]
        candidates = [(v, i) for i, v in enumerate(row) if v is not None]
        if not candidates:
            continue
        higher = _HIGHER_IS_BETTER.get(name, True)
        chosen = max(candidates) if higher else min(candidates)
        best[name] = labels[chosen[1]]

    return {
        "models_or_runs": models_or_runs,
        "labels": labels,
        "metrics": values,
        "deltas": deltas,
        "best": best,
    }


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

@_serialised
def sweep(
    workspace: Workspace,
    model_id: str,
    selector: str,
    field: str,
    values: list[Any],
    *,
    num_runs: int = 5,
    metrics: list[str] | None = None,
    abort: Any = None,
    progress: Any = None,
) -> dict:
    """Sweep a parameter across values, on copies of the model."""
    from raptor.api.build import update_element

    if not values:
        raise ValueError("sweep needs at least one value")
    if num_runs < 1:
        raise ValueError("num_runs must be at least 1")
    source = workspace.get(model_id)
    rows: list[dict] = []
    created: list[str] = []
    try:
        for step, val in enumerate(values):
            if abort is not None and abort.is_set():
                raise RunCancelledError("sweep was cancelled")
            if progress is not None:
                progress(step, len(values))
            copy_result = workspace.create_model(
                f"sweep_{selector}_{val}", template="blank"
            )
            copy_id = copy_result["model_id"]
            created.append(copy_id)
            copy_handle = workspace.get(copy_id)
            copy_handle.rbd = copy.deepcopy(source.rbd)
            copy_handle.rbd.file_name = f"sweep_{val}"
            # A copy, not an edit: the source's own state is not re-judged.
            # The swept value itself goes through `update_element` below,
            # so a value Raptor 7 would refuse is refused for the sweep too.
            workspace.save(copy_id, check_rules=False)

            update_element(workspace, copy_id, selector, {field: val})
            result = run(workspace, copy_id, num_runs=num_runs,
                         timeout_s=120, confirm_long_run=True,
                         abort=abort)

            # Provenance: the run that produced this row, and how many
            # replications it actually completed (a sweep row whose runs
            # < num_runs was silently truncated by the timeout).
            row: dict[str, Any] = {"value": val, "run_id": result.run_id,
                                   "runs": result.num_runs}
            wanted = {m.lower() for m in metrics} if metrics else None
            for m in result.metrics:
                if wanted is None or m.name.lower() in wanted:
                    row[m.name] = m.mean
            rows.append(row)
            workspace.close(copy_id, delete=True)
            created.remove(copy_id)
    finally:
        # Never leave THIS call's sweep copies behind on error. A name
        # prefix is not ownership: a user model called sweep_Casing_1
        # must not be deleted by an unrelated sweep.
        for cid in created:
            try:
                workspace.close(cid, delete=True)
            except Exception:
                pass

    monotonic = _is_monotonic(rows)
    warnings: list[str] = []
    identical = _all_rows_identical(rows)
    if identical:
        warnings.append(
            "Every sweep row has identical metric values -- either the "
            "parameter genuinely has no effect, or it was not applied. "
            "Check the per-row run_id and the field name."
        )
    short = [r for r in rows if r.get("runs") is not None and r["runs"] < num_runs]
    if short:
        warnings.append(
            f"{len(short)} of {len(rows)} rows completed fewer than "
            f"{num_runs} runs (timeout); their estimates are noisier than "
            f"requested."
        )
    return {
        "selector": selector,
        "field": field,
        "rows": rows,
        "monotonic": monotonic,
        "all_rows_identical": identical,
        "warnings": warnings,
    }


def _all_rows_identical(rows: list[dict]) -> bool:
    """True when every row's metric values are bit-identical (so a reader
    cannot tell "no effect" from "value never applied")."""
    if len(rows) < 2:
        return False
    metric_keys = [k for k in rows[0] if k not in _NON_METRIC_KEYS]
    if not metric_keys:
        return False
    first = rows[0]
    return all(
        all(r.get(k) == first.get(k) for k in metric_keys)
        for r in rows[1:]
    )


_NON_METRIC_KEYS = frozenset({"value", "run_id", "runs", "warnings"})


def _is_monotonic(rows: list[dict]) -> bool:
    if len(rows) < 2:
        return True
    metric = next((k for k in rows[0] if k not in _NON_METRIC_KEYS), None)
    if metric is None:
        return True
    vals = [r.get(metric) for r in rows]
    increasing = all(
        b is None or a is None or b >= a
        for a, b in itertools.pairwise(vals)
    )
    decreasing = all(
        b is None or a is None or b <= a
        for a, b in itertools.pairwise(vals)
    )
    return increasing or decreasing

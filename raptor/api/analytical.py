"""Qt-free analytical-solver API.

The CLI, MCP server and GUI all call `analyse`; none of them import the BDD
or survival modules directly. Results are JSON-safe (undefined values are
``None``, never NaN/Infinity).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Collection, Sequence
from typing import Any

from raptor.api.workspace import Workspace
from raptor.core.analytical import build as build_analytical
from raptor.core.analytical import recursion_headroom
from raptor.core.analytical import evaluate
from raptor.core.analytical.bdd import MAX_BDD_NODES
from raptor.core.analytical.errors import AnalyticalError, BddTooLarge, NotApplicable
from raptor.core.analytical.network import (
    DEPENDENCY_MODES,
    MAX_VARIABLES,
    STANDBY_MODES,
    has_dependency,
    has_node_dependency,
)
from raptor.core.engine.load import load_model

SCHEMA = "raptor-analytic/1"


def _safe_text(value: object) -> str:
    text = str(value).encode("utf-8", "replace").decode("utf-8")
    return text if len(text) <= 256 else text[:253] + "..."


def _finite_number(value: float, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _finite(value: float | None, label: str) -> float | None:
    return None if value is None else _finite_number(value, label)


def analyse(  # noqa: C901, PLR0912
    workspace: Workspace,
    model_id: str,
    *,
    mode: str = "reliability",
    hierarchy: str | int | None = None,
    at: float | None = None,
    times: Sequence[float] | None = None,
    bx: Sequence[float] | None = None,
    tau: float | None = None,
    importance: bool = True,
    equation: bool = False,
    ignore_repairs: bool = False,
    ignore: Collection[str] = (),
    phase: int | None = None,
    standby: str | None = None,
    dependencies: str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    max_variables: int = MAX_VARIABLES,
    max_bdd_nodes: int = MAX_BDD_NODES,
) -> dict[str, Any]:
    """Run the static solver for one model handle.

    ``progress(stage, done, total)`` is optional and may be called from a
    worker thread. ``max_variables`` and ``max_bdd_nodes`` override the
    solver defaults for this call.

    Raises `NotApplicable` when the model is outside the solver's assumptions;
    callers should show that message, not a traceback.
    """
    if mode not in ("reliability", "availability"):
        raise ValueError("mode must be 'reliability' or 'availability'")
    handle = workspace.get(model_id)
    return analyse_rbd(
        handle.rbd, handle.name,
        mode=mode, hierarchy=hierarchy, at=at, times=times, bx=bx, tau=tau,
        importance=importance, equation=equation,
        ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
        standby=standby, dependencies=dependencies,
        progress=progress,
        max_variables=max_variables, max_bdd_nodes=max_bdd_nodes,
    )


def analyse_rbd(
    rbd,
    name: str = "model",
    *,
    mode: str = "reliability",
    hierarchy: str | int | None = None,
    at: float | None = None,
    times: Sequence[float] | None = None,
    bx: Sequence[float] | None = None,
    tau: float | None = None,
    importance: bool = True,
    equation: bool = False,
    ignore_repairs: bool = False,
    ignore: Collection[str] = (),
    phase: int | None = None,
    standby: str | None = None,
    dependencies: str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    max_variables: int = MAX_VARIABLES,
    max_bdd_nodes: int = MAX_BDD_NODES,
) -> dict[str, Any]:
    """Analyse an in-memory ``RbdFile`` (the GUI's open model).

    ``hierarchy`` makes the analysis a port extension: that hierarchy's In
    node becomes Start, its Out node becomes End, and everything outside is
    dropped (see `raptor.api.isolate`)."""
    if hierarchy is not None:
        from raptor.api.isolate import IsolationError, extract
        try:
            rbd, record = extract(rbd, hierarchy)
        except IsolationError as exc:
            raise NotApplicable(str(exc)) from exc
        name = f"{name}:{record.hierarchy}"
    try:
        with recursion_headroom():
            result = _analyse_rbd(
                rbd, name, mode=mode, at=at, times=times, bx=bx, tau=tau,
                importance=importance, equation=equation,
                ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
                standby=standby, dependencies=dependencies,
                progress=progress,
                max_variables=max_variables, max_bdd_nodes=max_bdd_nodes)
        if hierarchy is not None:
            result.setdefault("scope", {})["hierarchy"] = record.hierarchy
            result["hierarchy"] = record.hierarchy
        return result
    except RecursionError as exc:
        raise NotApplicable(
            "the diagram is nested too deeply for the analytical solver; "
            "use simulation") from exc


def _analyse_rbd(  # noqa: C901, PLR0912, PLR0915
    rbd,
    name: str = "model",
    *,
    mode: str = "reliability",
    at: float | None = None,
    times: Sequence[float] | None = None,
    bx: Sequence[float] | None = None,
    tau: float | None = None,
    importance: bool = True,
    equation: bool = False,
    ignore_repairs: bool = False,
    ignore: Collection[str] = (),
    phase: int | None = None,
    standby: str | None = None,
    dependencies: str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    max_variables: int = MAX_VARIABLES,
    max_bdd_nodes: int = MAX_BDD_NODES,
) -> dict[str, Any]:
    if mode not in ("reliability", "availability"):
        raise ValueError("mode must be 'reliability' or 'availability'")
    # Choice arguments are the caller's input, not a property of the model:
    # reject a bad value as ValueError before `build()` wraps model-time
    # evaluation errors as NotApplicable.
    if standby is not None and standby not in STANDBY_MODES:
        raise ValueError(f"standby must be one of {list(STANDBY_MODES)}")
    if dependencies is not None and dependencies not in DEPENDENCY_MODES:
        raise ValueError(
            f"dependencies must be one of {list(DEPENDENCY_MODES)}")
    at = _finite(at, "mission time")
    tau = _finite(tau, "tau")
    if int(max_bdd_nodes) > MAX_BDD_NODES:
        raise ValueError(
            f"max_bdd_nodes cannot exceed the hard cap "
            f"{MAX_BDD_NODES:,}")
    if times is not None:
        if len(times) > 250:
            raise ValueError("at most 250 curve times are accepted")
        times = [_finite_number(point, "time") for point in times]
    if bx is not None:
        if len(bx) > 20:
            raise ValueError("at most 20 BX percentages are accepted")
        checked: list[float] = []
        for percent in bx:
            number = _finite_number(percent, "BX percentage")
            if not 0.0 <= number <= 100.0:
                raise ValueError("BX percentage must be in [0, 100]")
            checked.append(number)
        bx = checked
    try:
        from raptor.api.validate import issues
        problems = [issue for issue in issues(rbd)
                    if issue.severity == "error"]
        if problems:
            first = problems[0]
            raise NotApplicable(
                f"the model has {len(problems)} validation error(s); "
                f"first: {first.message}")
        model = load_model(rbd)
    except NotApplicable:
        raise
    except Exception as exc:  # engine refusal / malformed model
        raise NotApplicable(f"cannot load the model for analysis: {exc}") from exc

    try:
        built = build_analytical(
            model, mode=mode,
            ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
            standby=standby, dependencies=dependencies,
            progress=progress,
            max_variables=max_variables, max_bdd_nodes=max_bdd_nodes)
    except NotApplicable:
        raise
    except (OverflowError, ZeroDivisionError, ValueError) as exc:
        raise NotApplicable(
            f"the model's distribution parameters cannot be evaluated "
            f"analytically: {exc}") from exc
    if at is None:
        if mode == "availability":
            at = 0.0
        else:
            at = built.horizon
    if at is None:
        raise ValueError(
            "the model is not time-terminated, so there is no default "
            "mission time; give the mission time (at)")
    if at < 0.0:
        raise ValueError("at must be non-negative")

    units = ""
    try:
        units = str(rbd.sections["SYSTEM_DATA"].rows[0].get("units", "")).strip()
    except (KeyError, IndexError, AttributeError):
        pass   # a model with no SYSTEM_DATA row simply has no units
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "units": units,
        "model": _safe_text(name),
        "mode": mode,
        "applicable": True,
        "time": float(at) if mode == "reliability" else None,
        "assumptions": list(built.assumptions),
        "warnings": list(built.warnings),
        "bdd_nodes": built.node_count,
        "scope": _scope_dict(model, built.scope),
    }

    if progress is not None:
        progress("Evaluating results", 0, 1)
    if mode == "reliability":
        reliability = evaluate.system_probability(built, at)
        result["reliability"] = _num(reliability)
        result["pdf"] = _num(evaluate.pdf(built, at))
        result["hazard"] = _num(evaluate.hazard(built, at))
        result["mttf"] = _num(evaluate.mttf(built))
    else:
        availability = evaluate.availability(built)
        result["availability"] = _num(availability)
        result["unavailability"] = _num(1.0 - availability)
    if progress is not None:
        progress("Evaluating results", 1, 1)

    requested_bx = list(bx) if bx is not None else [10.0]
    if mode == "reliability" and requested_bx:
        result["bx"] = {
            _bx_key(percent): _num(evaluate.bx_life(built, percent))
            for percent in requested_bx
        }

    if times and mode == "availability":
        result["warnings"].append(
            "curve times are ignored in availability mode: the steady-state "
            "availability does not depend on time")
    elif times:
        points: list[dict[str, Any]] = []
        for point in times:
            if point < 0.0:
                raise ValueError("times must be non-negative")
            points.append({
                "time": float(point),
                "value": _num(evaluate.system_probability(built, point)),
            })
        result["times"] = points

    if tau is not None:
        if mode != "reliability":
            raise ValueError("conditional reliability is reliability-mode only")
        result["conditional"] = {
            "age": float(at),
            "tau": float(tau),
            "reliability": _num(evaluate.conditional_reliability(built, at, tau)),
        }

    result["components"] = _components(built, at)

    if importance:
        rows = evaluate.importance(
            built, 0.0 if mode == "availability" else at,
            progress=progress)
        result["importance"] = [_importance_row(row) for row in rows]
    else:
        # The MCP output schema requires the key; an empty list is the
        # honest value when importance was not requested.
        result["importance"] = []

    if equation:
        text = evaluate.equation(built)
        result["equation"] = text
        if text is None:
            result["warnings"].append(
                "the equation has too many paths or characters to display; "
                "use the numeric result or a coarser model")

    return _safe(result)


def _scope_dict(model, scope) -> dict[str, Any]:
    phase = scope.phase
    return {
        "ignore": sorted(scope.ignore),
        "phase": phase,
        "phase_name": (model.phases[phase - 1].phase_name
                       if phase is not None else None),
        "standby": scope.standby,
        "dependencies": scope.dependencies,
    }


def scope_options(rbd) -> dict[str, Any]:
    """What the model offers to ignore or choose: phase names, whether it has
    cold-standby nodes or dependencies. Lets a front end show only the
    controls that apply."""
    model = load_model(rbd)
    return {
        "phases": [phase.phase_name or f"Phase {i}"
                   for i, phase in enumerate(model.phases, start=1)],
        "has_standby": any(node.cold_standby or node.cold_links
                           for node in model.nodes_by_id.values()),
        "has_dependencies": (
            any(has_dependency(block)
                for block in model.blocks_by_id.values())
            or any(has_node_dependency(node)
                   for node in model.nodes_by_id.values())
        ),
        "hierarchies": [
            {"id": int(float(row["ID"])),
             "name": str(row.get("hierarchyName") or row["ID"])}
            for row in (rbd.sections.get("HIERARCHY_DATA").rows
                        if rbd.sections.get("HIERARCHY_DATA") else [])
            if str(row.get("ID", "0")) != "0"
        ],
    }


def _components(built, at: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for info in built.variables:
        row: dict[str, Any] = {
            "kind": info.kind,
            "id": info.id,
            "name": _safe_text(info.name),
            "perfect": info.perfect,
            "repairable": info.repairable,
            "mttf": _num(info.mean_fail),
            "mttr": _num(info.mean_down),
            "state": info.state,
        }
        if built.mode == "reliability":
            row["reliability"] = _num(info.reliability(at))
        else:
            row["availability"] = _num(info.availability)
        rows.append(row)
    return rows


def _importance_row(row: evaluate.ImportanceRow) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "id": row.id,
        "name": _safe_text(row.name),
        "probability": _num(row.probability),
        "birnbaum": _num(row.birnbaum),
        "raw": _num(row.raw),
        "rrw": _num(row.rrw),
        "vesely_fussell": _num(row.vesely_fussell),
        "criticality": _num(row.criticality),
    }


def _bx_key(percent: float) -> str:
    return str(int(percent)) if float(percent).is_integer() else str(percent)


def _num(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    value = float(value)
    return value if math.isfinite(value) else None


def _safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


__all__ = [
    "SCHEMA",
    "AnalyticalError",
    "BddTooLarge",
    "NotApplicable",
    "analyse",
    "scope_options",
]

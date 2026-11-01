"""Qt-free analytical-solver API.

The CLI, MCP server and GUI all call `analyse`; none of them import the BDD
or survival modules directly. Results are JSON-safe (undefined values are
``None``, never NaN/Infinity).
"""
from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from typing import Any

from raptor.api.workspace import Workspace
from raptor.core.analytical import build as build_analytical
from raptor.core.analytical import recursion_headroom
from raptor.core.analytical import evaluate
from raptor.core.analytical.errors import AnalyticalError, BddTooLarge, NotApplicable
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
) -> dict[str, Any]:
    """Run the static solver for one model handle.

    Raises `NotApplicable` when the model is outside the solver's assumptions;
    callers should show that message, not a traceback.
    """
    if mode not in ("reliability", "availability"):
        raise ValueError("mode must be 'reliability' or 'availability'")
    handle = workspace.get(model_id)
    return analyse_rbd(
        handle.rbd, handle.name,
        mode=mode, at=at, times=times, bx=bx, tau=tau,
        importance=importance, equation=equation,
        ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
        standby=standby,
    )


def analyse_rbd(
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
) -> dict[str, Any]:
    """Analyse an in-memory ``RbdFile`` (the GUI's open model)."""
    try:
        with recursion_headroom():
            return _analyse_rbd(
                rbd, name, mode=mode, at=at, times=times, bx=bx, tau=tau,
                importance=importance, equation=equation,
                ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
                standby=standby)
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
) -> dict[str, Any]:
    if mode not in ("reliability", "availability"):
        raise ValueError("mode must be 'reliability' or 'availability'")
    at = _finite(at, "mission time")
    tau = _finite(tau, "tau")
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
        built = build_analytical(model, mode=mode,
                                 ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
                standby=standby)
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
        rows = evaluate.importance(built, 0.0 if mode == "availability" else at)
        result["importance"] = [_importance_row(row) for row in rows]

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
    }


def scope_options(rbd) -> dict[str, Any]:
    """What the model offers to ignore or choose: phase names, whether it has
    cold-standby nodes. Lets a front end show only the controls that apply."""
    model = load_model(rbd)
    return {
        "phases": [phase.phase_name or f"Phase {i}"
                   for i, phase in enumerate(model.phases, start=1)],
        "has_standby": any(node.cold_standby or node.cold_links
                           for node in model.nodes_by_id.values()),
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

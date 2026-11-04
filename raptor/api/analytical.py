"""Qt-free analytical-solver API.

The CLI, MCP server and GUI all call `analyse`; none of them import the BDD
or survival modules directly. Results are JSON-safe (undefined values are
``None``, never NaN/Infinity).
"""
from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Collection, Sequence
from typing import Any

from raptor.api.workspace import Workspace
from raptor.core.analytical import build as build_analytical
from raptor.core.analytical import recursion_headroom
from raptor.core.analytical import evaluate
from raptor.core.analytical.bdd import MAX_BDD_NODES
from raptor.core.analytical.limits import max_bdd_nodes_allowed, memory_text
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


#: Typical passes MTTF's adaptive integration makes (434-1,361 seen on the
#: example and BESS models); only a starting guess for the time estimate.
_MTTF_PASSES_GUESS = 900
#: Passes one BX life search makes: one at time zero, up to 60 doubling
#: candidates, then about 9 walks of 32 points (about 355 seen on BESS).
_BX_PASSES = 360


#: Seconds to build one BDD node over seconds to evaluate one node in one
#: pass, measured 66-300 on the example and BESS models (2026-11-04: about
#: 1.5 us a node to build; 0.005-0.024 us a node-pass to evaluate, batched).
#: It is what makes one progress figure possible before the size is known:
#: the build's share of the work is about RATIO / (RATIO + passes), whatever
#: the diagram's size.
_BUILD_TO_PASS_COST = 100.0


def _pass_plan(mode: str, bx: Sequence[float] | None,
               times: Sequence[float] | None, tau: float | None,
               importance: bool, variables: int) -> dict[str, int]:
    """Walks over the diagram each stage of the results is expected to make."""
    reliability = mode == "reliability"
    return {"base": 4 if reliability else 1,
            "mttf": _MTTF_PASSES_GUESS if reliability else 0,
            "bx": _BX_PASSES * (len(bx) if bx is not None else 1)
            if reliability else 0,
            "curve": len(times or ()) if reliability else 0,
            "conditional": 2 if tau is not None else 0,
            "importance": 2 * variables + 1 if importance else 0}


class _Overall:
    """One progress figure, 0-1000, for the build and the results together.

    The build is given its expected share of the time (see
    `_BUILD_TO_PASS_COST`), the results the rest, so a caller shows one bar
    rather than one that fills and then starts again. The figure never goes
    back. The build's time left covers the results to come as well.
    """

    SCALE = 1000
    _BUILD_RATE = re.compile(r", roughly [^—]*? left at this rate")

    def __init__(self, progress: Callable[[str, int, int], None],
                 passes: int) -> None:
        self._progress = progress
        self._passes = max(1, passes)
        self.share = _BUILD_TO_PASS_COST / (_BUILD_TO_PASS_COST + self._passes)
        self._shown = 0
        self._began = time.monotonic()

    def _report(self, text: str, fraction: float) -> None:
        self._shown = max(self._shown,
                          min(self.SCALE, int(fraction * self.SCALE)))
        self._progress(text, self._shown, self.SCALE)

    def build(self, stage: str, done: int, total: int) -> None:
        if stage.startswith("Outlook: "):
            self._progress(stage, 0, 0)
            return
        # "Preparing model" (1 of 1 when done) is not the build's progress;
        # the graph nodes joined while building are.
        building = stage.startswith("Building")
        part = min(1.0, done / total) if building and total > 0 else 0.0
        text = self._BUILD_RATE.sub("", stage)
        elapsed = time.monotonic() - self._began
        if elapsed > 2.0 and 0.05 <= part < 1.0:
            projected = elapsed / part
            left = (projected * (1.0 - part)
                    + projected * self._passes / _BUILD_TO_PASS_COST)
            text = text.replace(")", f"), about {_duration(left)} left in all",
                                1) if ")" in text else \
                f"{text}, about {_duration(left)} left in all"
        self._report(text, self.share * part)

    def results(self, text: str, passes: int, total: int) -> None:
        part = min(1.0, passes / total) if total > 0 else 0.0
        self._report(text, self.share + (1.0 - self.share) * part)


def _duration(seconds: float) -> str:
    """A rough time in words: "about 40 s", "about 3 min"."""
    if seconds < 1.5:
        return "under 2 s"
    if seconds < 90:
        return f"about {round(seconds)} s"
    if seconds < 5400:
        return f"about {round(seconds / 60)} min"
    return f"about {seconds / 3600:.1f} h"


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


#: Options that only reliability mode uses; `analyse_modes` leaves them out
#: of an availability solve rather than have it warn or refuse.
_RELIABILITY_ONLY = ("at", "times", "bx", "tau", "ignore_repairs")


def analyse_modes(
    workspace: Workspace,
    model_id: str,
    modes: Sequence[str],
    *,
    progress: Callable[[str, int, int], None] | None = None,
    **options: Any,
) -> list[dict[str, Any]]:
    """`analyse` once per mode with the same options, for one workbook or
    report with reliability and availability side by side.

    ``phase="all"`` solves every phase on its own as well: one result per
    mode and phase, mode by mode, phases in order. Reliability-only options
    (mission time, curve times, BX, tau, ignoring repairs) are passed to
    the reliability solves only. Progress is one figure over all the
    solves, each stage named after its mode and phase.
    """
    handle = workspace.get(model_id)
    return analyse_rbd_set(handle.rbd, handle.name, modes,
                           progress=progress, **options)


def analyse_rbd_set(
    rbd,
    name: str,
    modes: Sequence[str],
    *,
    progress: Callable[[str, int, int], None] | None = None,
    **options: Any,
) -> list[dict[str, Any]]:
    """`analyse_modes` for an in-memory model (the window solves a copy)."""
    wanted = list(dict.fromkeys(modes))
    if not wanted or any(m not in ("reliability", "availability")
                         for m in wanted):
        raise ValueError("modes must be 'reliability' and/or 'availability'")
    phases: list[int | None] = [options.pop("phase", None)]
    if phases == ["all"]:
        count = len(scope_options(rbd)["phases"])
        if count == 0:
            raise ValueError("phase='all' needs a phased model; this one "
                             "has no phases")
        phases = list(range(1, count + 1))
    solves = [(mode, phase) for mode in wanted for phase in phases]
    results = []
    for index, (mode, phase) in enumerate(solves):
        chosen = dict(options, phase=phase)
        if mode == "availability":
            for key in _RELIABILITY_ONLY:
                chosen.pop(key, None)
            chosen["ignore"] = [item for item in chosen.get("ignore") or ()
                                if item != "repairs"]
        report: Callable[..., None] | None = progress
        if progress is not None and len(solves) > 1:
            label = (mode.capitalize() if len(wanted) > 1 else "") + (
                f"{', ' if len(wanted) > 1 else ''}phase {phase} of "
                f"{len(phases)}" if len(phases) > 1 else "")

            def report(stage: str, done: int, total: int, *,  # noqa: PLR0913
                       _index: int = index, _label: str = label) -> None:
                if total <= 0:
                    progress(stage, done, total)
                    return
                scale = 1000
                overall = (_index * scale + done * scale // total) // len(solves)
                progress(f"{_label}: {stage}", overall, scale)
        results.append(analyse_rbd(rbd, name, mode=mode, progress=report,
                                   **chosen))
    return results


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

    ``hierarchy`` analyses that one hierarchy on its own: its In node is
    treated as Start and its Out node as End, so its input is assumed always
    available, and everything outside it is left out (see
    `raptor.api.isolate`). Raptor 7 has no equivalent."""
    if hierarchy is not None:
        from raptor.api.isolate import extract
        # Pass the dependency choice into extraction: an outside dependency
        # must be dropped for `down`, not silently rewritten as "up" before
        # the solver sees it. `IsolationError` is a ValueError, so the CLI
        # reports a bad selector as a usage error and the GUI as a setting.
        # `up` is the optimistic extraction default. `down` cannot be
        # represented after extraction has already removed an outside
        # dependency, so refuse the boundary crossing instead of silently
        # returning the `up` answer.
        treatments = ({"deps": "refuse"} if dependencies == "down"
                      else {"deps": "up"} if dependencies == "up" else None)
        rbd, record = extract(rbd, hierarchy, treatments)
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
    if int(max_variables) < 1:
        raise ValueError("max_variables must be at least 1")
    if int(max_bdd_nodes) < 2:
        raise ValueError("max_bdd_nodes must be at least 2")
    allowed = max_bdd_nodes_allowed()
    if int(max_bdd_nodes) > allowed:
        raise ValueError(
            f"a BDD limit of {int(max_bdd_nodes):,} nodes would need "
            f"{memory_text(int(max_bdd_nodes))} at most; this machine allows "
            f"up to {allowed:,} ({memory_text(allowed)}), half of the memory "
            "that is free now")
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

    overall = None
    if progress is not None:
        # The element count before the build stands in for the variables
        # (the same, less any the build drops) to plan the passes.
        overall = _Overall(progress, sum(_pass_plan(
            mode, bx, times, tau, importance,
            len(model.blocks_by_id) + len(model.events_by_id)).values()))
    try:
        built = build_analytical(
            model, mode=mode,
            ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
            standby=standby, dependencies=dependencies,
            progress=overall.build if overall is not None else None,
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

    # Report (and give the caller a chance to cancel) during evaluation
    # too: on a large diagram the results take far longer than the build,
    # because MTTF, the curve and importance walk the diagram many times.
    # Each pass costs about the same, so the passes still to come give a
    # time estimate. Importance (2 per element + 1) and the curve (1 per
    # time) are exact; MTTF and BX are adaptive, so their shares are
    # guesses. A stage that runs past its share has the share raised, and a
    # finished stage's share becomes what it used, so an overrun never eats
    # into the stages still to come (it once left the count at "pass N of
    # about N" before importance had begun). Throttled to a few reports a
    # second.
    # The figure reported is `_Overall`'s: the build's share, then the
    # results' share in proportion to the passes.
    variables = sum(1 for info in built.variables if info.variable is not None)
    plan = _pass_plan(mode, bx, times, tau, importance, variables)
    report = overall.results if overall is not None else None
    stage = ["Evaluating results"]
    share = ["base"]
    passes = [0]
    stage_began = [0]
    last = [0.0]
    began = [0.0]

    def on_pass(count: int = 1) -> None:
        if passes[0] == 0:
            began[0] = time.monotonic()
        passes[0] += count
        now = time.monotonic()
        used = passes[0] - stage_began[0]
        if used > plan[share[0]]:
            plan[share[0]] = int(used * 1.25) + 1
        if report is None or now - last[0] < 0.2:
            return
        last[0] = now
        total = max(passes[0], sum(plan.values()))
        text = f"{stage[0]} — pass {passes[0]:,} of about {total:,}"
        if passes[0] > 1 and now - began[0] > 1.0:
            per_pass = (now - began[0]) / (passes[0] - 1)
            text += f", {_duration(per_pass * (total - passes[0]))} left"
        report(text, passes[0], total)

    def enter(name: str, key: str) -> None:
        """Start a stage, and say so at once (a small model's passes all
        fall inside the throttle)."""
        if key != share[0]:
            plan[share[0]] = passes[0] - stage_began[0]
            share[0], stage_began[0] = key, passes[0]
        stage[0] = name
        if report is not None:
            report(name, passes[0], max(passes[0], sum(plan.values())))

    built.bdd.on_pass = on_pass
    if report is not None:
        report("Evaluating results", 0, 1)
    if mode == "reliability":
        enter("Working out reliability", "base")
        reliability = evaluate.system_probability(built, at)
        result["reliability"] = _num(reliability)
        result["pdf"] = _num(evaluate.pdf(built, at))
        result["hazard"] = _num(evaluate.hazard(built, at))
        enter("Working out MTTF", "mttf")
        result["mttf"] = _num(evaluate.mttf(built))
    else:
        availability = evaluate.availability(built)
        result["availability"] = _num(availability)
        result["unavailability"] = _num(1.0 - availability)

    requested_bx = list(bx) if bx is not None else [10.0]
    if mode == "reliability" and requested_bx:
        enter("Working out BX life", "bx")
        result["bx"] = {
            _bx_key(percent): _num(evaluate.bx_life(built, percent))
            for percent in requested_bx
        }

    if times and mode == "availability":
        result["warnings"].append(
            "curve times are ignored in availability mode: the steady-state "
            "availability does not depend on time")
    elif times:
        enter("Working out the curve", "curve")
        if any(point < 0.0 for point in times):
            raise ValueError("times must be non-negative")
        # Every curve point in one batched walk over the diagram.
        values = evaluate.system_probabilities(built, list(times))
        result["times"] = [{"time": float(point), "value": _num(value)}
                           for point, value in zip(times, values)]

    if tau is not None:
        if mode != "reliability":
            raise ValueError("conditional reliability is reliability-mode only")
        enter("Working out conditional reliability", "conditional")
        result["conditional"] = {
            "age": float(at),
            "tau": float(tau),
            "reliability": _num(evaluate.conditional_reliability(built, at, tau)),
        }

    result["components"] = _components(built, at)

    if importance:
        enter("Computing importance", "importance")
        # No per-element progress here: the pass reports above cover it
        # (two passes per element) with a time estimate.
        rows = evaluate.importance(
            built, 0.0 if mode == "availability" else at)
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

    if overall is not None:
        overall.results("Done", 1, 1)     # 100 %: the last report is throttled
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

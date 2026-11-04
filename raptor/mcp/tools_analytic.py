"""MCP tool: exact analytical reliability/availability for the static subset."""
from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.jobs import run_job
from raptor.mcp.result import ADDITIVE, READ_ONLY, guarded, markdown_table, ok
from raptor.mcp.server import mcp


def _scope_choice(value: object) -> str | None:
    """A solver scope choice, restricted to the values the solver emits."""
    return value if value in ("active", "absent", "up", "down") else None


def _markdown(result: dict) -> str:  # noqa: C901, PLR0912
    lines = [f"**{result['model']} — {result['mode']}**", ""]
    if result.get("hierarchy"):
        lines.append(f"- Scope: hierarchy {result['hierarchy']} on its "
                     "own (input assumed always available)")
        lines.append("")
    scope = result.get("scope") or {}
    choices = []
    if scope.get("phase") is not None:
        choices.append(f"phase {scope['phase']}")
    standby = _scope_choice(scope.get("standby"))
    if standby in ("active", "absent"):
        choices.append("standby paths " + (
            "fully in service" if standby == "active" else "absent"))
    dependencies = _scope_choice(scope.get("dependencies"))
    if dependencies in ("up", "down"):
        choices.append("dependencies " + dependencies)
    if choices:
        lines.append("- Assumptions: " + "; ".join(choices))
        lines.append("")
    if result["mode"] == "reliability":
        lines.append(f"- Mission time: {result.get('time')}")
        lines.append(f"- Reliability: {result.get('reliability')}")
        lines.append(f"- Density: {result.get('pdf')}")
        lines.append(f"- Hazard: {result.get('hazard')}")
        lines.append(f"- MTTF: {result.get('mttf')}")
        bx = result.get("bx") or {}
        if bx:
            lines.append("- BX life: " + ", ".join(
                f"B{k}={v}" for k, v in bx.items()))
        conditional = result.get("conditional")
        if conditional:
            lines.append(
                f"- Conditional reliability, {conditional['tau']} more after "
                f"{conditional['age']}: {conditional['reliability']}")
    else:
        lines.append(f"- Availability: {result.get('availability')}")
        lines.append(f"- Unavailability: {result.get('unavailability')}")
    if result.get("times"):
        lines += ["", markdown_table(
            ["Time", "Reliability"],
            [[row["time"], row["value"]] for row in result["times"]])]
    if result["mode"] == "availability" and result.get("components"):
        lines += ["", markdown_table(
            ["Element", "Availability", "MTTF", "MTTR"],
            [[row["name"], row.get("availability"), row.get("mttf"),
              row.get("mttr")] for row in result["components"]])]
    if result.get("warnings"):
        lines.append("")
        lines.append("Warnings: " + "; ".join(result["warnings"]))
    importance = result.get("importance") or []
    if importance:
        lines += ["", markdown_table(
            ["Element", "Birnbaum", "RAW", "RRW", "VF", "Criticality"],
            [[row["name"], row.get("birnbaum"), row.get("raw"),
              row.get("rrw"), row.get("vesely_fussell"),
              row.get("criticality")] for row in importance])]
    if result.get("equation"):
        lines += ["", "```", result["equation"], "```"]
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY("Analytical analysis"))
@guarded
async def analytical_analysis(
    model_id: str,
    mode: Literal["reliability", "availability"] = "reliability",
    hierarchy: str | int | None = None,
    at: float | None = None,
    times: list[float] | None = None,
    bx: list[float] | None = None,
    tau: float | None = None,
    importance: bool = True,
    equation: bool = False,
    ignore_repairs: bool = False,
    ignore: list[Literal["repairs", "pm", "spares", "resources"]] | None = None,
    phase: int | None = None,
    standby: Literal["active", "absent"] | None = None,
    dependencies: Literal["up", "down"] | None = None,
    max_variables: int | None = None,
    max_bdd_nodes: int | None = None,
    ctx: Context | None = None,
) -> CallToolResult:
    """Solve the static subset of a model exactly.

    **Reliability mode** is non-repairable: `R(t)`, density, hazard, MTTF,
    BX life, conditional reliability and exact importance. **Availability
    mode** is steady state for independent components with perfect repair:
    `A_i = MTTF_i / (MTTF_i + MTTR_i)` and exact availability importance.

    The solver supports series, parallel, mixed, k-of-n, bridge networks and
    hierarchies. `hierarchy` analyses one hierarchy on its own: its In
    node is treated as Start and its Out node as End, so its input is
    assumed always available. It refuses
    PM, spares, resources and non-Bernoulli events, and refuses standby,
    phases or dependencies until you say how to treat them; use `simulate`
    for the behaviours it still cannot represent. `standby="active"` keeps
    cold-standby paths in service and `standby="absent"` removes them;
    `phase` analyses a phased model as if it stayed in that phase.
    `dependencies="up"` analyses each dependent element's own law as if the
    dependency never blocked it, and `dependencies="down"` removes dependent
    elements from the success path. Without `dependencies`, a model with
    dependencies is refused. `at` defaults to the model's time-terminated
    run length. `tau` adds the conditional reliability of a further mission
    of that length after `at`.
    `ignore_repairs` (same as `ignore=["repairs"]`) gives the first-failure
    reliability of a repairable model.

    The default solver limits are 5,000 blocks/events and 2,000,000 BDD
    nodes (about 0.7 GB). `max_variables` and `max_bdd_nodes` override them;
    `max_bdd_nodes` may go up to half of the machine's free memory at about
    350 bytes a node, and a higher value is refused. Before building, a
    progress message gives a size outlook: whether any junction's inputs
    share blocks (the only way the diagram grows large) and which. Raising a
    limit does not guarantee a solution, and the solver refuses rather than
    approximate; solving one hierarchy at a time is usually better.

    A long solve sends MCP progress notifications about once a second: one
    percentage for the whole solve (building the diagram, then the
    results), with the stage and the time left in the message.
    `export_analytical` writes the same result to a spreadsheet.
    """
    from raptor.api.analytical import analyse_modes

    options = _options(
        hierarchy=hierarchy, at=at, times=times, bx=bx, tau=tau,
        importance=importance, equation=equation,
        ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
        standby=standby, dependencies=dependencies,
        max_variables=max_variables, max_bdd_nodes=max_bdd_nodes)

    def work(abort, progress):
        return analyse_modes(context.workspace(), model_id, [mode],
                             progress=_reporter(abort, progress,
                                                "analytical_analysis"),
                             **options)[0]

    result = await run_job(ctx, work, label="Analysing")
    return ok(result, markdown=_markdown(result))


def _options(*, ignore, max_variables, max_bdd_nodes, **chosen) -> dict:
    """The solver options both tools pass on."""
    options = dict(chosen, ignore=list(ignore or ()))
    if max_variables is not None:
        options["max_variables"] = max_variables
    if max_bdd_nodes is not None:
        options["max_bdd_nodes"] = max_bdd_nodes
    return options


def _reporter(abort, progress, tool: str):
    def report(stage: str, done: int, total: int) -> None:
        # A cancelled call must stop the solve, not leave it running on a
        # worker thread: the solver checks in here many times a second.
        if abort.is_set():
            raise RuntimeError(f"{tool} was cancelled")
        progress(done, total or None, stage)
    return report


@mcp.tool(annotations=ADDITIVE("Export analytical result"))
@guarded
async def export_analytical(
    model_id: str,
    modes: list[Literal["reliability", "availability"]] | None = None,
    folder: str | None = None,
    hierarchy: str | int | None = None,
    at: float | None = None,
    times: list[float] | None = None,
    bx: list[float] | None = None,
    tau: float | None = None,
    importance: bool = True,
    equation: bool = False,
    ignore_repairs: bool = False,
    ignore: list[Literal["repairs", "pm", "spares", "resources"]] | None = None,
    phase: int | Literal["all"] | None = None,
    standby: Literal["active", "absent"] | None = None,
    dependencies: Literal["up", "down"] | None = None,
    max_variables: int | None = None,
    max_bdd_nodes: int | None = None,
    ctx: Context | None = None,
) -> CallToolResult:
    """Solve exactly, as `analytical_analysis` does, and write a spreadsheet.

    `modes` (default `["reliability"]`): one or both of reliability and
    availability, solved with the same settings into ONE workbook, a set of
    sheets per mode (Summary, Elements with the importance measures, Curve,
    Equation, Assumptions). Reliability-only settings (`at`, `times`, `bx`,
    `tau`, ignoring repairs) apply to the reliability solve only.
    `phase="all"` solves every phase of a phased model on its own, a set of
    sheets per mode and phase ("Phase 2 Summary").

    The file is `<model>[_h<hierarchy>]_analytical_<modes>[_all_phases |
    _phase<N>].xlsx`, e.g.
    `Plant_analytical_reliability_availability.xlsx`, in `folder` (inside
    the workspace; default `Tables/<model>`). An existing file of that name
    is replaced. Every other argument is as for `analytical_analysis`.

    Returns {path, modes, results: [{mode, phase, reliability |
    availability}]}.
    """
    context.require_write()
    from raptor.api.analytical import analyse_modes
    from raptor.api.analytical_export import export_file_name, export_xlsx
    from raptor.api.table_export import TABLES_DIRNAME, safe_stem

    ws = context.workspace()
    handle = ws.get(model_id)
    if folder is not None:
        folder = context.require_relative_path(folder, "folder")
    dest = ws.resolve_output_path(
        folder or f"{TABLES_DIRNAME}/{safe_stem(handle.name)}")
    for managed in ("models", "runs", "renders"):
        root = (ws.root / managed).resolve()
        if dest == root or root in dest.parents:
            raise ValueError(f"export folder cannot be inside the {managed} "
                             "tree; choose a folder under Tables/")
    options = _options(
        hierarchy=hierarchy, at=at, times=times, bx=bx, tau=tau,
        importance=importance, equation=equation,
        ignore_repairs=ignore_repairs, ignore=ignore, phase=phase,
        standby=standby, dependencies=dependencies,
        max_variables=max_variables, max_bdd_nodes=max_bdd_nodes)
    wanted = list(modes or ["reliability"])

    def work(abort, progress):
        return analyse_modes(ws, model_id, wanted,
                             progress=_reporter(abort, progress,
                                                "export_analytical"),
                             **options)

    results = await run_job(ctx, work, label="Analysing")
    dest.mkdir(parents=True, exist_ok=True)
    path = export_xlsx(results, dest / export_file_name(handle.name, results))
    summary = [{"mode": r["mode"],
                "phase": (r.get("scope") or {}).get("phase"),
                **({"reliability": r.get("reliability")}
                   if r["mode"] == "reliability"
                   else {"availability": r.get("availability")})}
               for r in results]
    data = {"path": str(path),
            "modes": list(dict.fromkeys(r["mode"] for r in results)),
            "results": summary, "resource_links": [f"file://{path}"]}
    lines = [f"Wrote `{path.name}` to `{path.parent}`:", ""]
    lines += [f"- {row['mode']}"
              + (f", phase {row['phase']}" if row["phase"] is not None else "")
              + f": {row.get('reliability', row.get('availability'))}"
              for row in summary]
    return ok(data, markdown="\n".join(lines))

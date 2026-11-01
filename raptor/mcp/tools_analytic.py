"""MCP tool: exact analytical reliability/availability for the static subset."""
from __future__ import annotations

from typing import Literal

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import READ_ONLY, guarded, markdown_table, ok
from raptor.mcp.server import mcp


def _markdown(result: dict) -> str:  # noqa: C901
    lines = [f"**{result['model']} — {result['mode']}**", ""]
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
def analytical_analysis(
    model_id: str,
    mode: Literal["reliability", "availability"] = "reliability",
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
) -> CallToolResult:
    """Solve the static subset of a model exactly.

    **Reliability mode** is non-repairable: `R(t)`, density, hazard, MTTF,
    BX life, conditional reliability and exact importance. **Availability
    mode** is steady state for independent components with perfect repair:
    `A_i = MTTF_i / (MTTF_i + MTTR_i)` and exact availability importance.

    The solver supports series, parallel, mixed, k-of-n, bridge networks and
    hierarchies. It refuses PM, spares, resources, dependencies, standby,
    phases and non-Bernoulli events; use `simulate` for those. `at` defaults
    to the model's time-terminated run length. `tau` adds the conditional
    reliability of a further mission of that length after `at`.
    `ignore_repairs` (same as `ignore=["repairs"]`) gives the first-failure
    reliability of a repairable model.
    """
    from raptor.api.analytical import analyse

    result = analyse(
        context.workspace(), model_id,
        mode=mode, at=at, times=times, bx=bx, tau=tau,
        importance=importance, equation=equation,
        ignore_repairs=ignore_repairs, ignore=ignore or (), phase=phase,
        standby=standby,
    )
    return ok(result, markdown=_markdown(result))

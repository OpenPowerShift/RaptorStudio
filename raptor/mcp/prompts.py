"""MCP prompts — user-pickable workflows.

Three workflows:

    new_availability_study  build -> validate -> run -> render -> report
    explain_model           walk a model's topology and flag SPFs
    improve_availability    run, find the weak link, sweep, compare
"""
from __future__ import annotations

from raptor.mcp.server import mcp


@mcp.prompt()
def new_availability_study(system_description: str = "",
                           mission_hours: float = 8760.0) -> str:
    """Start a new availability study from a plain-English description.

    Builds the model, validates it, estimates and runs it, renders the
    diagram, and reports availability plus single points of failure.
    """
    description = system_description or (
        "a pump station with two duty pumps and one standby, "
        "exponential failures with a 1000-hour mean, 8-hour lognormal repairs"
    )
    return (
        f"Start a new availability study for {description}. "
        f"Use a mission time of {mission_hours:g} hours.\n\n"
        "Steps: build the model with `build_model`, run `validate_model`, "
        "call `estimate_runtime`, then `simulate`. Render the diagram with "
        "`render_diagram`, list `single_point_failures`, and present the "
        "results as Markdown tables with units in the headers. "
        "Do not invent fault-tree features."
    )


@mcp.prompt()
def explain_model(model_id: str = "") -> str:
    """Explain an existing model in plain English.

    Describes the topology, then flags single points of failure.
    """
    target = model_id or "<ask the user which model_id, or call list_models>"
    return (
        f"Explain the model {target} in plain English: walk its topology "
        "(series / parallel / k-of-n paths), describe each element's failure "
        "and repair behaviour, then call `single_point_failures` and explain "
        "which elements would take the system down on their own."
    )


@mcp.prompt()
def improve_availability(model_id: str = "", target: float = 0.99) -> str:
    """Propose and test changes that improve availability.

    Runs the model, finds the weak link, sweeps a couple of changes, and
    compares them against the baseline.
    """
    target_id = model_id or "<ask the user which model_id, or call list_models>"
    return (
        f"For model {target_id}, target availability {target:g}. "
        "Run it, identify the weak link, then use `sweep_parameter` (or "
        "edited copies plus `compare_models`) to test two or three changes "
        "such as shorter repairs, extra spares, or a standby unit. "
        "Present a side-by-side comparison with deltas."
    )

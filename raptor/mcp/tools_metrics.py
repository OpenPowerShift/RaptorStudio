"""MCP tool: definitions and statistics for RAPTOR's result metrics.

Read-only. It explains what the engine reports; it does not calculate new
reliability results.
"""
from __future__ import annotations

import re
from typing import Any

from mcp.types import CallToolResult

from raptor.mcp import context
from raptor.mcp.result import READ_ONLY, guarded, markdown_table, ok
from raptor.mcp.server import mcp

_SAFE_ID = re.compile(r"[A-Za-z0-9_\-]+")


def _load_summary(run_id: str,
                  model_id: str | None = None) -> tuple[dict[str, Any],
                                                       list[Any] | None]:
    """One stored run's summary and per-run rows, validated.

    The file reads and identity checks live in `raptor.api.run_tables`
    (the same code `bundle_from_run` uses); this only adds the metrics
    precondition, which is specific to this tool.
    """
    from raptor.api.run import run_dir
    from raptor.api.run_tables import read_per_run, read_summary
    from raptor.api.workspace import RunArtefactsError

    if not _SAFE_ID.fullmatch(run_id):
        shown = run_id if len(run_id) <= 80 else run_id[:77] + "..."
        raise ValueError(f"invalid run_id {shown!r}")
    runs_dir = run_dir(context.workspace(), run_id)
    summary = read_summary(runs_dir, run_id, model_id=model_id, required=True)
    if not isinstance(summary.get("metrics"), dict):
        raise RunArtefactsError(
            f"run {run_id!r} has no usable metric summary; re-run it")
    return summary, read_per_run(runs_dir, run_id)


def _run_view(run_id: str, model_id: str | None) -> dict[str, Any]:
    """Validated metric view for one stored run."""
    from raptor.api import metric_definitions
    from raptor.api.workspace import RunArtefactsError

    summary, per_run = _load_summary(run_id, model_id)
    stored_model = summary.get("model_id")
    runs = summary.get("num_runs")
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 0:
        runs = None
    if per_run is not None and runs is not None and len(per_run) != runs:
        raise RunArtefactsError(
            f"run {run_id!r} has incomplete per-run data; re-run it")
    samples = (metric_definitions.samples_from_per_run(per_run)
               if per_run is not None else None)
    view = metric_definitions.from_summary(
        summary.get("metrics", {}),
        units=(str(summary.get("units"))
               if isinstance(summary.get("units"), str) else ""),
        runs=runs,
        samples=samples,
    )
    if view.get("runs") is None:
        view.pop("runs", None)
    view["run_id"] = run_id
    if isinstance(stored_model, str):
        view["model_id"] = stored_model
    if isinstance(summary.get("model_hash"), str):
        view["model_hash"] = summary["model_hash"]
    isolation = summary.get("isolation")
    if isolation is not None and not isinstance(isolation, dict):
        raise RunArtefactsError(
            f"run {run_id!r} has corrupt isolation metadata; re-run it")
    if isolation:
        view["isolation"] = isolation
    return view


@mcp.tool(annotations=READ_ONLY("Describe metrics"))
@guarded
def describe_metrics(run_id: str | None = None,
                     model_id: str | None = None) -> CallToolResult:
    """List the result metrics, and optionally show one run's values.

    Without `run_id`, returns the name and units of every metric RAPTOR
    reports: availability, reliability, conditional reliability,
    dependability, MTBDE/MDT, MTBM/MTBMs/MTBMu, MMT/MMTs/MMTu, system and
    component failures, and green/yellow/red percentages. **Definitions and
    equations are not repeated here** -- call `read_doc('formulas')` for
    them; that page is the single source. With `run_id`, also returns that
    run's sample count, min/mean/max, standard deviation and standard
    error. `model_id` is optional and only checks that the run belongs to
    that model.

    These are the engine's existing outputs; no analytical or alternative
    solution method is applied. Use `get_report` or `get_table` for the
    underlying data. Censored MTBDE/MTBM values are lower bounds and are
    marked with `>` in the Markdown view.
    """
    from raptor.api import metric_definitions

    data: dict[str, Any] = {"metrics": metric_definitions.definitions()}
    names = ", ".join(row["metric"] for row in data["metrics"])
    md = ("**Metrics RAPTOR reports**\n\n" + names + "\n\n"
          "Definitions and equations are in the formulas page -- "
          "`read_doc('formulas')` returns it. They are deliberately not "
          "restated here: this tool used to carry its own copy and the two "
          "had drifted.")

    if run_id is not None:
        view = _run_view(run_id, model_id)
        data = {**data, **view}

        def show(value: float | None, unit: str = "",
                 censored: bool = False) -> str:
            suffix = f" {unit}" if unit else ""
            if value is None:
                return "—"
            return (">" if censored else "") + f"{value:,.6g}{suffix}"

        rows = []
        for row in view["metrics"]:
            unit = row["units"]
            censored = bool(row.get("censored"))
            samples = row.get("samples")
            rows.append([
                row["metric"],
                "—" if samples is None else str(samples),
                show(row["min"], unit),
                show(row["mean"], unit, censored),
                show(row["max"], unit, censored),
                "—" if censored or row["stdev"] is None
                else f"{row['stdev']:.4g}",
                "—" if censored or row["sem"] is None
                else f"{row['sem']:.4g}",
            ])
        if view.get("isolation"):
            md += (f"\n\n**Isolated hierarchy** — "
                   f"{view['isolation'].get('hierarchy', '')}. "
                   "Not a whole-model result.")
        md += "\n\n**Values for this run**\n\n" + markdown_table(
            ["Measure", "N", "Min", "Mean", "Max", "Std. dev.", "SEM"],
            rows)
        if view.get("notes"):
            md += "\n\n" + "\n".join(f"_{n}_" for n in view["notes"])

    return ok(data, markdown=md)

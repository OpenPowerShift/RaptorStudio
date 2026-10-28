"""Illustrative Pydantic models for MCP tool inputs.

NOTE: the MCP SDK builds each tool's actual input schema from the tool
function's own signature (see ``tools_model``/``tools_run``/``tools_view``);
these models are documentation/reference and are not wired into the server.
When they disagree, the function signature is authoritative. Kept in sync
for readers; see ``render_diagram`` for the current example.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Workspace & lifecycle ────────────────────────────────────

class OpenModelInput(BaseModel):
    path: str = Field(description="Workspace-relative file path, or an example name; absolute paths are refused")


class CreateModelInput(BaseModel):
    name: str = Field(description="Human-readable model name")
    template: str = Field(
        default="blank",
        description="Template: 'blank', 'series', 'parallel', or 'k_of_n'",
    )


class SaveModelAsInput(BaseModel):
    model_id: str
    path: str = Field(description="Workspace-relative destination path")
    format: str = Field(default="rbds", description="'rbds' or 'rbd'")


class CloseModelInput(BaseModel):
    model_id: str | list[str] = Field(
        description="One model_id or a list of them"
    )
    delete: bool = Field(
        default=False,
        description="Delete the model's own files (irreversible; backups included)",
    )


# ── Building & editing ───────────────────────────────────────

class BuildModelInput(BaseModel):
    name: str = Field(description="Model name")
    spec: dict[str, Any] = Field(
        description="The .rbds JSON document with friendly fields"
    )
    auto_layout: bool = Field(default=True)
    validate_model: bool = Field(
        default=True,
        description="Run validation after building",
    )


class DescribeModelInput(BaseModel):
    model_id: str
    detail: str = Field(default="summary", description="'summary' or 'full'")
    level: int = Field(
        default=0, description="Hierarchy id to describe (0 = whole model)"
    )


class AddBlockInput(BaseModel):
    model_id: str
    name: str = Field(description="Block name")
    fail_dist: int | None = Field(default=None, description="Fail distribution code")
    fail_params: list[float | str] | None = Field(
        default=None,
        description="Fail params — numbers or expression strings with # comments",
    )
    repair_dist: int | None = Field(default=None, description="Repair distribution code")
    repair_params: list[float | str] | None = Field(
        default=None,
        description="Repair params — numbers or expression strings",
    )
    parent: int = Field(default=0, description="Hierarchy parent ID")
    after: str | None = Field(
        default=None,
        description="Element name/id to wire this block after",
    )


class AddNodeInput(BaseModel):
    model_id: str
    type: str = Field(description="'start', 'regular', 'end', 'in', or 'out'")
    k: int = Field(
        default=-1,
        description=(
            "K-out-of-N threshold. -1 is RAPTOR's 'never set' sentinel and "
            "behaves as 1-of-N (NOT 'all paths'); use k = the node's incoming "
            "link count for an all-required node."
        ),
    )
    name: str = Field(default="", description="Node name")
    parent: int = Field(default=0)


class AddEventInput(BaseModel):
    model_id: str
    name: str
    prob: float = Field(default=1.0, description="Event probability (0-1)")


class ConnectInput(BaseModel):
    model_id: str
    from_element: Any = Field(
        alias="from",
        description='Source: ["Block", 3] or "PumpA"',
    )
    to_element: Any = Field(
        alias="to",
        description='Target: ["Node", 2] or "End"',
    )

    model_config = {"populate_by_name": True}


class UpdateElementInput(BaseModel):
    model_id: str
    selector: str = Field(description="Element id or name")
    patch: dict[str, Any] = Field(
        description="Fields to update — any .rbds field incl. sparing, PM, costs",
    )


class DeleteElementInput(BaseModel):
    model_id: str
    selector: str
    reconnect: bool = Field(default=True)


class UndoLastEditInput(BaseModel):
    model_id: str


class AutoLayoutInput(BaseModel):
    model_id: str = Field(description="Model to re-arrange on the layered grid")


# ── Model features ───────────────────────────────────────────

class ConfigureSparingInput(BaseModel):
    model_id: str
    selector: str = Field(description="Block name or id")
    mode: str = Field(description="'infinite', 'custom', or 'pool'")
    stock: int | None = None
    restock_every: float | None = None
    emergency: float | None = None
    slo: int | None = None


class ConfigureMaintenanceInput(BaseModel):
    model_id: str
    selector: str = Field(description="Block name or id")
    pm_freq: float | None = None
    pm_duration_dist: int | None = None
    pm_params: list[float] | None = None
    trigger_name: str | None = None
    defer_flags: dict[str, bool] | None = None


class ConfigurePhasesInput(BaseModel):
    model_id: str
    phases: list[dict[str, Any]] = Field(
        description="Phase definitions: [{name, duration_dist, params, mission}]"
    )
    assignments: dict[str, list] | None = None


class ConfigureSystemInput(BaseModel):
    model_id: str
    run_length: float | None = None
    num_runs: int | None = None
    termination: str | None = None
    start_stats: float | None = None
    units: str | None = None
    weak_link: bool | None = None
    cost: bool | None = None
    capacity: bool | None = None
    gy_threshold: float | None = None
    yr_threshold: float | None = None
    system_streams: str | None = None
    display_unit: str | None = None
    flow_generated: float | None = Field(
        default=None, description="Demand injected at the Start node"
    )


class ManageRatesInput(BaseModel):
    model_id: str
    action: str = Field(description="'list', 'add', 'update', or 'delete'")
    name: str | None = None
    expr: str | None = Field(
        default=None,
        description="Expression like '0.00015 * 20000 / 8760  # basis'",
    )
    description: str | None = None


# ── Analysis ─────────────────────────────────────────────────

class ValidateModelInput(BaseModel):
    model_id: str


class EstimateRuntimeInput(BaseModel):
    model_id: str
    num_runs: int = Field(default=10)
    run_length: float | None = None
    workers: int = Field(default=1)
    importance: str | None = Field(
        default=None,
        description="Override the model's importance mode for this estimate",
    )


class SimulateInput(BaseModel):
    model_id: str
    num_runs: int | None = None
    run_length: float | None = None
    collect_events: bool = Field(default=False)
    workers: int = Field(default=1)
    date: str | None = None
    timeout_s: float = Field(default=1800.0)
    confirm_long_run: bool = Field(default=False)
    seed: int | None = Field(
        default=None,
        description="Optional non-negative seed; None = engine's fixed seeds",
    )


class GetReportInput(BaseModel):
    run_id: str
    section: str = Field(
        default="final_results",
        description="'final_results', 'end_of_run', 'events', etc.",
    )
    run_number: int | None = None
    format: str = Field(default="markdown", description="'markdown' or 'raw'")
    offset: int = Field(default=0)
    limit: int = Field(default=200)


class SinglePointFailuresInput(BaseModel):
    model_id: str
    workers: int = Field(default=1)
    limit: int = Field(default=100)
    offset: int = Field(default=0)


class FailureEffectsInput(BaseModel):
    model_id: str
    fail: list[str | int] = Field(description="Element names/ids to fail")
    repair: list[str | int] = Field(default_factory=list)


class CompareModelsInput(BaseModel):
    models: list[str] = Field(description="2-5 model_ids or run_ids")
    num_runs: int = Field(default=5)
    metrics: list[str] | None = None


# ── What-if ──────────────────────────────────────────────────

class SweepParameterInput(BaseModel):
    model_id: str
    selector: str = Field(description="Element name or id")
    field: str = Field(description="Field to sweep, e.g. 'repair_params[0]'")
    values: list[Any] = Field(description="Values to test (≤12)")
    num_runs: int = Field(default=5)
    metrics: list[str] | None = Field(
        default=None, description="Metrics to report; None = every metric"
    )


# ── Annotations (.rbds drawing overlays) ─────────────────────

class AddAnnotationInput(BaseModel):
    model_id: str
    kind: str = Field(default="text", description="'text', 'rect', 'ellipse' or 'arrow'")
    text: str = Field(default="")
    adjacent_to: str | None = Field(
        default=None, description="Element name/id to place next to (no coordinates)"
    )
    side: str = Field(default="right", description="'right', 'left', 'above' or 'below'")
    gap: float = Field(default=12.0, description="Gap from the anchor")
    x: float | None = Field(default=None, description="Explicit scene x (top-left)")
    y: float | None = Field(default=None, description="Explicit scene y (top-left)")
    w: float | None = None
    h: float | None = None
    from_: str | None = Field(default=None, description="Arrow tail element (note underscore)")
    to: str | None = Field(default=None, description="Arrow head element")
    x1: float | None = None
    y1: float | None = None
    x2: float | None = None
    y2: float | None = None
    parent: int | None = Field(default=None, description="Hierarchy id (defaults to anchor's)")
    style: dict[str, Any] | None = Field(
        default=None,
        description="border_colour, border_width, line_style, fill_colour, text_colour, font_*, z",
    )


class ListAnnotationsInput(BaseModel):
    model_id: str
    parent: int | None = Field(default=None, description="Filter to one hierarchy id")


class UpdateAnnotationInput(BaseModel):
    model_id: str
    annotation_id: int
    patch: dict[str, Any] = Field(
        description="Any style/text/geometry field, or a re-anchor (adjacent_to/side/gap)",
    )


class DeleteAnnotationInput(BaseModel):
    model_id: str
    annotation_id: int


# ── Diagram ──────────────────────────────────────────────────

class RenderDiagramInput(BaseModel):
    model_id: str
    level: int = Field(
        default=0,
        description="Hierarchy id to render inside (0 = whole model); not a depth index",
    )
    zoom_to: list[str] = Field(
        default_factory=list,
        description=(
            "Element names/ids to frame a close-up on, within the current "
            "level (composes with `level`); 'Kind:id' for an ambiguous id"
        ),
    )
    margin: float = Field(
        default=12.0, description="Scene-unit (not pixel) padding around the zoom box"
    )
    highlight: list[str] = Field(
        default_factory=list,
        description="Element names/ids to outline (e.g. SPF)",
    )
    theme: Literal["light", "dark"] = Field(default="light")
    max_px: int = Field(default=1600)
    include_annotations: bool = Field(default=True)


# ── Timeseries ──────────────────────────────────────────────

class GetTimeseriesInput(BaseModel):
    run_id: str
    series: str = Field(description="'availability', 'capacity', or 'cost'")
    runs: str = Field(default="mean", description="'mean', 'all', or a run number")
    points: int = Field(default=100, description="Max data points after decimation")


# ── Reference ───────────────────────────────────────────────

class SearchDocsInput(BaseModel):
    query: str
    limit: int = Field(default=5)


class DescribeMetricsInput(BaseModel):
    run_id: str | None = Field(
        default=None,
        description="Optional completed run whose statistics to include",
    )
    model_id: str | None = Field(
        default=None,
        description="Optional model id to check that the run belongs to it",
    )

"""Generate the MCP reference catalogue and keep the guide's facts current.

    .venv/bin/python tools/gen_mcp_reference.py          # rewrite both files
    .venv/bin/python tools/gen_mcp_reference.py --check  # exit 1 if either is stale

Two documents come out of the live server:

* ``docs/MCP-reference.md`` -- the exhaustive catalogue: every tool with its
  full server description, a schema-derived parameter table, its declared
  return fields and a copy-paste argument example, grouped into task
  categories.  Nothing here is hand-written except the category names, the
  examples, the return notes for tools without an output schema, and the
  prose around the generated sections.
* ``docs/MCP.md`` -- the hand-written guide.  Only the facts block between
  the generated markers is rewritten (tool, resource, prompt and chapter
  counts), so a count cannot drift from the server or the book.

The tool, resource and prompt data come from ``tools/list``,
``resources/list``, ``resources/templates/list`` and ``prompts/list``; the
book count comes from ``new_docs/book.adoc``'s include list.  Tool order is
category order, then name, so ``--check`` is byte-for-byte deterministic.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Running this file as a script puts tools/ on sys.path, not the checkout
# root, so an installed raptor-studio could shadow the checkout being
# documented.  The docs must describe this tree.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "MCP-reference.md"
GUIDE = ROOT / "docs" / "MCP.md"
BOOK = ROOT / "new_docs" / "book.adoc"

GUIDE_BEGIN = "<!-- BEGIN GENERATED FACTS (tools/gen_mcp_reference.py) -->"
GUIDE_END = "<!-- END GENERATED FACTS -->"

#: Display order.  ``slug`` is also the stable anchor: ``tools-<slug>``.
CATEGORIES: list[tuple[str, str, str]] = [
    ("workspace", "Workspace and lifecycle",
     "Find bundled examples, open or create a model, and manage its "
     "workspace lifetime."),
    ("build-edit", "Build and edit",
     "Create a whole model from one spec, edit elements and links, "
     "configure behaviour, and step through undo history."),
    ("analysis", "Analysis",
     "Check a model, solve its static subset exactly, test failures, and "
     "measure which parts matter."),
    ("what-if", "Simulation and what-if",
     "Run simulations, estimate or control long batches, sweep a parameter "
     "and compare models or runs."),
    ("diagram", "Diagram and annotations",
     "Lay out the diagram, add notes and callouts, and render a picture."),
    ("results", "Results and tables",
     "Read, page, plot and export the numbers from a completed run."),
    ("meta", "Meta and documentation",
     "Discover distributions and read the documentation the server ships."),
]

#: Every registered tool belongs to exactly one category.  A new tool is a
#: deliberate change: add it here, give it an example below, and regenerate.
TOOL_CATEGORY: dict[str, str] = {
    # Workspace and lifecycle
    "ping": "workspace",
    "workspace_info": "workspace",
    "list_models": "workspace",
    "open_model": "workspace",
    "create_model": "workspace",
    "save_model_as": "workspace",
    "close_model": "workspace",
    "delete_file": "workspace",
    # Build and edit
    "build_model": "build-edit",
    "describe_model": "build-edit",
    "add_block": "build-edit",
    "add_node": "build-edit",
    "add_event": "build-edit",
    "add_elements": "build-edit",
    "connect": "build-edit",
    "connect_many": "build-edit",
    "update_element": "build-edit",
    "update_elements": "build-edit",
    "delete_element": "build-edit",
    "delete_elements": "build-edit",
    "delete_link": "build-edit",
    "move_element": "build-edit",
    "add_hierarchy": "build-edit",
    "collapse_selection": "build-edit",
    "undo_last_edit": "build-edit",
    "redo_last_edit": "build-edit",
    "configure_block_defaults": "build-edit",
    "manage_rates": "build-edit",
    "configure_sparing": "build-edit",
    "configure_maintenance": "build-edit",
    "configure_phases": "build-edit",
    "configure_system": "build-edit",
    "manage_spare_pools": "build-edit",
    "manage_resources": "build-edit",
    "manage_triggers": "build-edit",
    "get_element": "build-edit",
    "diff_models": "build-edit",
    "extract_hierarchy": "build-edit",
    # Analysis
    "validate_model": "analysis",
    "analytical_analysis": "analysis",
    "describe_metrics": "analysis",
    "single_point_failures": "analysis",
    "failure_effects": "analysis",
    "failure_effects_batch": "analysis",
    # Simulation and what-if
    "estimate_runtime": "what-if",
    "simulate": "what-if",
    "start_run": "what-if",
    "run_status": "what-if",
    "cancel_run": "what-if",
    "compare_models": "what-if",
    "sweep_parameter": "what-if",
    # Diagram and annotations
    "auto_layout": "diagram",
    "add_annotation": "diagram",
    "list_annotations": "diagram",
    "update_annotation": "diagram",
    "delete_annotation": "diagram",
    "render_diagram": "diagram",
    "render_diagram_svg": "diagram",
    # Results and tables
    "get_run_results": "results",
    "get_report": "results",
    "get_timeseries": "results",
    "list_tables": "results",
    "get_table": "results",
    "export_tables": "results",
    "export_analytical": "results",
    # Meta and documentation
    "list_distributions": "meta",
    "read_doc": "meta",
    "search_docs": "meta",
}

#: A short, valid argument object for every tool.  ``<model_id>`` and
#: ``<run_id>`` stand for ids returned by another call; everything else is
#: literal so a reader can paste it into an MCP client's tool form.
EXAMPLES: dict[str, dict[str, Any]] = {
    "ping": {},
    "workspace_info": {},
    "list_models": {"include_examples": True},
    "open_model": {"path": "MotorDemo"},
    "create_model": {"name": "Pump station", "template": "series", "n": 3},
    "save_model_as": {"model_id": "<model_id>",
                      "path": "exports/pump-station.rbds"},
    "close_model": {"model_id": "<model_id>"},
    "delete_file": {"path": "exports/old-model.rbds"},
    "build_model": {
        "name": "Quickstart",
        "spec": {
            "blocks": [{
                "name": "Pump",
                "fail_dist": "Exponential",
                "fail_params": [1000],
                "repair_dist": "Exponential",
                "repair_params": [10],
            }],
            "nodes": [{"name": "Start", "type": "start"},
                      {"name": "End", "type": "end"}],
            "links": [{"from": "Start", "to": "Pump"},
                      {"from": "Pump", "to": "End"}],
        },
    },
    "describe_model": {"model_id": "<model_id>", "detail": "full"},
    "add_block": {"model_id": "<model_id>", "name": "Pump",
                  "fail_dist": "Exponential", "fail_params": [1000],
                  "repair_dist": "Exponential", "repair_params": [10]},
    "add_node": {"model_id": "<model_id>", "type": "regular",
                 "name": "Join", "k": 2},
    "add_event": {"model_id": "<model_id>", "name": "Operator available",
                  "prob": 0.98},
    "add_elements": {
        "model_id": "<model_id>",
        "elements": [
            {"kind": "block", "name": "Valve", "fail_dist": "Exponential",
             "fail_params": [5000]},
            {"kind": "event", "name": "Power available", "prob": 0.999},
        ],
    },
    "connect": {"model_id": "<model_id>", "from_element": "Start",
                "to_element": "Pump"},
    "connect_many": {
        "model_id": "<model_id>",
        "links": [{"from": "Pump", "to": "Valve"},
                  {"from": "Valve", "to": "End"}],
    },
    "update_element": {"model_id": "<model_id>", "selector": "Pump",
                       "patch": {"fail_params": [1200]}},
    "update_elements": {
        "model_id": "<model_id>",
        "updates": [
            {"selector": "Pump", "patch": {"name": "Pump A"}},
            {"selector": "Valve", "patch": {"fail_params": [6000]}},
        ],
    },
    "delete_element": {"model_id": "<model_id>", "selector": "Valve"},
    "delete_elements": {"model_id": "<model_id>",
                        "selectors": ["Block:3", "Event:Operator available"]},
    "delete_link": {"model_id": "<model_id>", "link_id": "5"},
    "move_element": {"model_id": "<model_id>", "selector": "Pump",
                     "parent": 900},
    "add_hierarchy": {"model_id": "<model_id>", "name": "Cooling loop"},
    "collapse_selection": {"model_id": "<model_id>",
                           "selectors": ["Pump", "Valve"],
                           "name": "Cooling loop"},
    "undo_last_edit": {"model_id": "<model_id>"},
    "redo_last_edit": {"model_id": "<model_id>"},
    "configure_block_defaults": {"action": "set", "fail_dist": 4,
                                 "fail_params": [1000],
                                 "repair_dist": 4, "repair_params": [10]},
    "manage_rates": {"model_id": "<model_id>", "action": "add",
                     "name": "Pump failure rate", "expr": "1/1000",
                     "description": "Pump MTBF is 1,000 h"},
    "configure_sparing": {"model_id": "<model_id>", "selector": "Pump",
                          "mode": "pool", "spare_pool": "Pumps"},
    "configure_maintenance": {"model_id": "<model_id>", "selector": "Pump",
                              "pm_freq": 720, "pm_duration_dist": 4,
                              "pm_params": [4]},
    "configure_phases": {
        "model_id": "<model_id>",
        "phases": [{"name": "Cruise", "duration_dist": "Fixed",
                    "params": [10]}],
        "mode": "add",
    },
    "configure_system": {"model_id": "<model_id>", "run_length": 8760,
                         "num_runs": 200, "capacity": True, "cost": True,
                         "weak_link": True, "flow_generated": 100},
    "manage_spare_pools": {"model_id": "<model_id>", "action": "add",
                           "name": "Pumps", "stock": 2,
                           "arrive_every": 720},
    "manage_resources": {"model_id": "<model_id>", "action": "add",
                         "name": "Crew", "num_in": 2, "per_time_unit": 45},
    "manage_triggers": {"model_id": "<model_id>", "action": "add",
                        "name": "Inspection", "dist": "Normal",
                        "params": [720, 24], "init_used": 0},
    "get_element": {"model_id": "<model_id>", "selector": "Pump",
                    "detail": "full"},
    "diff_models": {"model_a": "<model_id_a>", "model_b": "<model_id_b>",
                    "detail": "summary"},
    "extract_hierarchy": {"model_id": "<model_id>",
                          "hierarchy": "Cooling loop", "report_only": True},
    "validate_model": {"model_id": "<model_id>"},
    "analytical_analysis": {"model_id": "<model_id>", "mode": "reliability",
                            "at": 500, "importance": True},
    "describe_metrics": {"run_id": "<run_id>"},
    "single_point_failures": {"model_id": "<model_id>"},
    "failure_effects": {"model_id": "<model_id>", "fail": ["Pump"]},
    "failure_effects_batch": {
        "model_id": "<model_id>",
        "scenarios": [
            {"fail": ["Pump"]},
            {"fail": ["Pump", "Valve"]},
            {"fail": ["Pump"], "repair": ["Valve"]},
        ],
    },
    "estimate_runtime": {"model_id": "<model_id>", "num_runs": 200,
                         "importance": "both"},
    "simulate": {"model_id": "<model_id>", "num_runs": 200,
                 "importance": "both", "seed": 7},
    "start_run": {"model_id": "<model_id>", "num_runs": 1000, "workers": 1},
    "run_status": {"job_id": "<job_id>"},
    "cancel_run": {"job_id": "<job_id>"},
    "compare_models": {"models": ["<model_id_a>", "<model_id_b>"],
                       "num_runs": 20},
    "sweep_parameter": {"model_id": "<model_id>", "selector": "Pump",
                        "field": "repair_params[0]",
                        "values": [4, 8, 16, 32], "num_runs": 20},
    "auto_layout": {"model_id": "<model_id>"},
    "add_annotation": {"model_id": "<model_id>", "kind": "text",
                       "text": "Pump is the weak link",
                       "adjacent_to": "Pump", "location": "above"},
    "list_annotations": {"model_id": "<model_id>"},
    "update_annotation": {"model_id": "<model_id>", "annotation_id": 1,
                          "patch": {"text": "Updated note"}},
    "delete_annotation": {"model_id": "<model_id>", "annotation_id": 1},
    "render_diagram": {"model_id": "<model_id>", "theme": "light"},
    "render_diagram_svg": {"model_id": "<model_id>", "routing": "orthogonal"},
    "get_run_results": {"run_id": "<run_id>", "top": 10},
    "get_report": {"run_id": "<run_id>", "section": "final_results",
                   "limit": 200},
    "get_timeseries": {"run_id": "<run_id>", "series": "availability",
                       "points": 100},
    "list_tables": {"model_id": "<model_id>", "run_id": "<run_id>"},
    "get_table": {"model_id": "<model_id>", "table": "results.summary",
                  "run_id": "<run_id>"},
    "export_tables": {"model_id": "<model_id>", "run_id": "<run_id>",
                      "formats": ["xlsx"]},
    "export_analytical": {"model_id": "<model_id>",
                          "modes": ["reliability", "availability"],
                          "phase": "all"},
    "list_distributions": {},
    "read_doc": {"name": "dependency"},
    "search_docs": {"query": "capacity analysis", "limit": 5},
}


def _first_paragraph(text: str | None) -> str:
    """One table cell from the first paragraph of a description."""
    para = (text or "").strip().split("\n\n")[0]
    return " ".join(para.split()).replace("|", "\\|")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "any"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _schema_types(schema: Any) -> tuple[list[str], list[Any]]:
    """Return ``(type labels, enum/const values)`` for a JSON Schema node."""
    if not isinstance(schema, dict):
        return ["any"], []
    if "anyOf" in schema:
        types: list[str] = []
        values: list[Any] = []
        for alternative in schema["anyOf"]:
            alt_types, alt_values = _schema_types(alternative)
            types.extend(alt_types)
            values.extend(alt_values)
        return _dedupe(types), values
    if "const" in schema:
        return [_json_type(schema["const"])], [schema["const"]]
    if "enum" in schema:
        values = list(schema["enum"])
        return _dedupe([_json_type(v) for v in values]), values

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return _dedupe([str(t) for t in schema_type]), []
    if schema_type == "array":
        item_types, item_values = _schema_types(schema.get("items", {}))
        rendered = " \\| ".join(item_types) if item_types else "any"
        return [f"array<{rendered}>"], item_values
    if schema_type == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            value_types, _ = _schema_types(extra)
            rendered = " \\| ".join(value_types) if value_types else "any"
            return [f"object<string, {rendered}>"], []
        return ["object"], []
    if schema_type:
        return [str(schema_type)], []
    return ["any"], []


def _type_label(schema: Any) -> str:
    types, _ = _schema_types(schema)
    return " \\| ".join(types) if types else "any"


def _value_label(value: Any) -> str:
    if isinstance(value, str):
        return f"`{value}`"
    if value is None:
        return "`null`"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    return f"`{json.dumps(value)}`"


def _enum_label(values: list[Any]) -> str:
    unique: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return ", ".join(_value_label(v) for v in unique) if unique else "—"


def _default_label(schema: dict[str, Any]) -> str:
    if "default" not in schema:
        return "—"
    return _value_label(schema["default"])


def _parameters(schema: dict[str, Any]) -> tuple[list[str], list[str]]:
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not properties:
        return [], []
    lines = [
        "| Parameter | Type | Required | Default | Enum / allowed values |",
        "| --- | --- | --- | --- | --- |",
    ]
    notes: list[str] = []
    for name in sorted(properties):
        prop = properties[name]
        _, values = _schema_types(prop)
        lines.append(
            f"| `{name}` | {_type_label(prop)} | "
            f"{'yes' if name in required else 'no'} | "
            f"{_default_label(prop)} | {_enum_label(values)} |"
        )
        note = " ".join(str(prop.get("description") or "").split())
        if note:
            notes.append(f"- `{name}` — {note}")
    return lines, notes


#: Tools whose result shape is documented in prose but has no declared
#: output schema.  The catalogue must still give a reader the return summary,
#: so keep these next to the examples and check them in the docs test.
RETURN_NOTES: dict[str, str] = {
    "delete_link": "`{deleted, kind, id}` for the removed link.",
    "move_element": "`{moved, parent, element}` — the selector, its new "
                    "parent and the moved element row.",
    "update_elements": "`{updated, results: [{selector, id, name, kind, "
                       "changed}]}`.",
    "connect_many": "`{connected, links: [{link_id, from, to}]}`.",
    "add_elements": "`{added, elements: [{kind, id, name}]}`.",
    "delete_elements": "`{deleted: [{selector, kind}], links_removed}`.",
    "manage_spare_pools": "`{pools, count}` for `action='list'`; the "
                          "affected pool for add/update/delete.",
    "manage_resources": "`{resources, count}` for `action='list'`; the "
                        "affected resource for add/update/delete.",
    "manage_triggers": "`{triggers, count}` for `action='list'`; the "
                       "affected trigger for add/update/delete.",
    "start_run": "`{job_id, state, phase, setup, requested_runs, workers, "
                 "workers_auto, ...}`.  Poll `run_status` for progress and "
                 "the finished `run_id`.",
    "run_status": "`{job_id, model_id, state, phase, setup, completed_runs, "
                  "requested_runs, percent, elapsed_s, wall_seconds, run_id, "
                  "summary, error}`.",
    "cancel_run": "The job snapshot, with the same fields as `run_status`; "
                  "poll until `state` is `cancelled`.",
    "get_run_results": "`{run_id, model_id, model_name, runs, units, "
                       "metrics, capacity, per_run, weak_link, files}`.",
    "export_tables": "`{folder, tables, files: [path], resource_links}`.",
    "render_diagram": "Image content plus `{width, height, elements_drawn, "
                      "elements_in_view, px_per_unit, effective_label_px, "
                      "legible, png_path, resource_link}`.",
    "export_analytical": "`{path, modes, results: [{mode, phase, "
                         "reliability | availability}], resource_links}`.",
}


def _returns(schema: dict[str, Any] | None, name: str = "") -> list[str]:
    if not schema:
        note = RETURN_NOTES.get(name)
        if note:
            return [note]
        return [
            "No output schema is declared.  The result is a Markdown "
            "confirmation plus `structuredContent`; the tool description "
            "gives the shape for this tool."
        ]
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not properties:
        return [f"Returns `{_type_label(schema)}` with no declared top-level "
                "fields."]
    lines = [
        "| Field | Type | Always present |",
        "| --- | --- | --- |",
    ]
    for name in sorted(properties):
        lines.append(
            f"| `{name}` | {_type_label(properties[name])} | "
            f"{'yes' if name in required else 'no'} |"
        )
    return lines


def _kind(tool: Any) -> str:
    annotations = tool.annotations
    if annotations and annotations.read_only_hint:
        return "read-only"
    if annotations and annotations.destructive_hint:
        return "destructive"
    return "writes"


def _live() -> tuple[list[Any], list[Any], list[Any], list[Any]]:
    """Every live tool, resource, resource template and prompt."""
    from raptor.mcp import (  # noqa: F401 -- importing registers every tool
        prompts, resources, server, tools_analytic, tools_data, tools_model,
        tools_run, tools_metrics, tools_tables, tools_view, tools_svg,
    )

    tools = sorted(asyncio.run(server.mcp.list_tools()), key=lambda t: t.name)
    fixed = sorted(asyncio.run(server.mcp.list_resources()), key=lambda r: r.uri)
    templates = sorted(asyncio.run(server.mcp.list_resource_templates()),
                       key=lambda r: r.uri_template)
    prompt_list = sorted(asyncio.run(server.mcp.list_prompts()),
                         key=lambda p: p.name)
    return tools, fixed, templates, prompt_list


def _book_facts() -> tuple[int, int]:
    """``(chapter count, position of the MCP chapter)`` from book.adoc."""
    text = BOOK.read_text(encoding="utf-8")
    chapters = [stem for stem in re.findall(
        r"include::chapters/([^.\[]+)\.adoc", text)
        if not stem.startswith("appendix-")]
    if "ch16-mcp" not in chapters:
        raise ValueError("new_docs/book.adoc no longer includes ch16-mcp.adoc")
    return len(chapters), chapters.index("ch16-mcp") + 1


def _facts() -> tuple[int, int, int, int, int, int]:
    tools, fixed, templates, prompts = _live()
    n_chapters, mcp_position = _book_facts()
    return (len(tools), len(tools) - (1 if any(
        t.name == "render_diagram" for t in tools) else 0),
        len(fixed) + len(templates), len(prompts), n_chapters, mcp_position)


def _validate_metadata(tools: list[Any]) -> None:
    names = [tool.name for tool in tools]
    missing_category = sorted(set(names) - set(TOOL_CATEGORY))
    stale_category = sorted(set(TOOL_CATEGORY) - set(names))
    missing_example = sorted(set(names) - set(EXAMPLES))
    stale_example = sorted(set(EXAMPLES) - set(names))
    missing_returns = sorted(
        tool.name for tool in tools
        if not tool.output_schema and tool.name not in RETURN_NOTES)
    problems = []
    if missing_category:
        problems.append("no category: " + ", ".join(missing_category))
    if stale_category:
        problems.append("category names a tool that is gone: "
                        + ", ".join(stale_category))
    if missing_example:
        problems.append("no example: " + ", ".join(missing_example))
    if stale_example:
        problems.append("example names a tool that is gone: "
                        + ", ".join(stale_example))
    if missing_returns:
        problems.append("no RETURN_NOTES entry (the tool declares no output "
                        "schema): " + ", ".join(missing_returns))
    if problems:
        raise ValueError(
            "tools/gen_mcp_reference.py metadata is incomplete; update "
            "CATEGORIES/TOOL_CATEGORY/EXAMPLES/RETURN_NOTES:\n  - " + "\n  - ".join(
                problems))


def render() -> str:
    """Render ``docs/MCP-reference.md`` from the live server."""
    tools, fixed, templates, prompts = _live()
    _validate_metadata(tools)
    n_chapters, mcp_position = _book_facts()
    n_without_render = len(tools) - (
        1 if any(tool.name == "render_diagram" for tool in tools) else 0)

    by_name = {tool.name: tool for tool in tools}
    grouped: list[tuple[str, str, str, list[Any]]] = []
    for slug, title, blurb in CATEGORIES:
        members = [by_name[name] for name in sorted(TOOL_CATEGORY)
                   if TOOL_CATEGORY[name] == slug]
        grouped.append((slug, title, blurb, members))

    lines = [
        "<!-- GENERATED by tools/gen_mcp_reference.py -- edit the template "
        "there, not this file. -->",
        "# MCP tool reference",
        "",
        f"This is the exhaustive catalogue generated from the live "
        f"`raptor-studio` server: **{len(tools)} tools** "
        f"({n_without_render} with `--no-render`), **"
        f"{len(fixed) + len(templates)} resources** and "
        f"**{len(prompts)} prompts**.  For installation, client setup, "
        "concepts and task recipes, start with the "
        "[MCP guide](MCP.md).",
        "",
        "Every tool section is generated from the server's `tools/list` "
        "payload.  It repeats the full description the assistant sees, "
        "turns the input schema into a typed parameter table, lists the "
        "declared return fields, and gives one argument example.  A "
        "trailing `?` is not used here: the **Required** column is "
        "authoritative.",
        "",
        "## Contents",
        "",
    ]
    for slug, title, _blurb, members in grouped:
        lines.append(f"- [{title}](#tools-{slug}) — {len(members)} tools")
        for tool in members:
            lines.append(f"  - [`{tool.name}`](#tool-{tool.name})")
    lines += [
        "",
        "## How to read a tool section",
        "",
        "- **Kind** is the MCP risk hint: `read-only` changes nothing, "
        "`writes` edits a model or workspace, and `destructive` deletes or "
        "reverts.  `--read-only` refuses writes and destructive tools.",
        "- **Parameters** comes from the tool's input schema.  `Required` "
        "means the server rejects the call without it; `Default` is the "
        "value the server uses when you omit it; `Enum / allowed values` "
        "lists every accepted value.",
        "- **Returns** lists the top-level fields of the declared output "
        "schema.  `Always present` is yes when the field is in the schema's "
        "`required` list.",
        "- **Example** is an argument object, not a whole JSON-RPC "
        "envelope.  Put it in your client's tool form or send it as the "
        "`arguments` of `tools/call`.  Replace `<model_id>`, `<run_id>`, "
        "`<job_id>` and `<model_id_a>`/`<model_id_b>` with ids from another "
        "call.",
        "",
        "## Tools by task",
        "",
    ]

    for slug, title, blurb, members in grouped:
        lines += [
            f'<a id="tools-{slug}"></a>',
            "",
            f"### {title}",
            "",
            blurb,
            "",
        ]
        for tool in members:
            description = (tool.description or "").strip()
            lines += [
                f'<a id="tool-{tool.name}"></a>',
                "",
                f"#### `{tool.name}`",
                "",
                f"- **Kind:** {_kind(tool)}",
                f"- **Group:** [{title}](#tools-{slug})",
                "",
                description,
                "",
                "**Parameters**",
                "",
            ]
            params, notes = _parameters(tool.input_schema or {})
            if params:
                lines += params
                if notes:
                    lines += ["", "Parameter notes:", ""] + notes
            else:
                lines.append("No parameters.")
            lines += ["", "**Returns**", ""]
            lines += _returns(tool.output_schema, tool.name)
            lines += [
                "",
                "**Example**",
                "",
                "```json",
                json.dumps(EXAMPLES[tool.name], indent=2),
                "```",
                "",
            ]

    lines += [
        '<a id="resources"></a>',
        "",
        "## Resources",
        "",
        "Resources are read with `resources/read`; templates take the "
        "arguments shown in the URI.",
        "",
        "| URI | MIME type | Contents |",
        "| --- | --- | --- |",
    ]
    for resource in fixed:
        lines.append(
            f"| `{resource.uri}` | `{resource.mime_type}` | "
            f"{_first_paragraph(resource.description)} |")
    for resource in templates:
        lines.append(
            f"| `{resource.uri_template}` | `{resource.mime_type}` | "
            f"{_first_paragraph(resource.description)} |")
    lines += [
        "",
        '<a id="prompts"></a>',
        "",
        "## Prompts",
        "",
        "Prompts are reusable starting points a client can offer as a "
        "slash command or template.",
        "",
        "| Prompt | Arguments | What it does |",
        "| --- | --- | --- |",
    ]
    for prompt in prompts:
        arguments = ", ".join(
            f"`{arg.name}`" for arg in prompt.arguments or []) or "—"
        lines.append(
            f"| `{prompt.name}` | {arguments} | "
            f"{_first_paragraph(prompt.description)} |")
    lines += [
        "",
        "## Book and guide",
        "",
        f"The tutorial book has **{n_chapters} chapters**; *RAPTOR from an "
        f"AI Assistant* is chapter 16, number {mcp_position} of "
        f"{n_chapters}.  The hand-written [MCP guide](MCP.md) covers "
        "install, client configuration, concepts and worked task recipes.",
        "",
        "## Reporting problems",
        "",
        "Open an issue at <https://github.com/openpowershift/RaptorStudio/"
        "issues> with the client, the tool call and the server's "
        "`--log-level DEBUG` output.  The tool list is a tested contract, "
        "so a change to it is deliberate and shows in the diff of this "
        "page.",
        "",
    ]
    return "\n".join(lines)


def render_guide() -> str:
    """Return ``docs/MCP.md`` with its generated facts block refreshed."""
    text = GUIDE.read_text(encoding="utf-8")
    if GUIDE_BEGIN not in text or GUIDE_END not in text:
        raise ValueError(
            f"{GUIDE} is missing the generated-facts markers; restore "
            f"{GUIDE_BEGIN} ... {GUIDE_END}")
    n_tools, n_without_render, n_resources, n_prompts, n_chapters, mcp_pos = (
        _facts())
    facts = "\n".join([
        GUIDE_BEGIN,
        f"- **{n_tools} tools** in {len(CATEGORIES)} task groups.  With "
        f"`--no-render`, which hides the PNG diagram tool, the server exposes "
        f"**{n_without_render}**.",
        f"- **{n_resources} resources** (fixed URIs and templates) and "
        f"**{n_prompts} prompts**.",
        f"- The tutorial book has **{n_chapters} chapters**; *RAPTOR from an "
        f"AI Assistant* is chapter 16, number {mcp_pos} of {n_chapters}.",
        GUIDE_END,
    ])
    before, rest = text.split(GUIDE_BEGIN, 1)
    _old, after = rest.split(GUIDE_END, 1)
    return before + facts + after


if __name__ == "__main__":
    if "--check" in sys.argv:
        reference_current = OUT.exists() and OUT.read_text(encoding="utf-8") == render()
        guide_current = (GUIDE.exists()
                         and GUIDE.read_text(encoding="utf-8") == render_guide())
        sys.exit(0 if reference_current and guide_current else 1)
    OUT.write_text(render(), encoding="utf-8")
    GUIDE.write_text(render_guide(), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"updated {GUIDE}")

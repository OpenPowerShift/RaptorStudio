#!/usr/bin/env python
"""Regenerate `raptor/mcp/output_schemas.json`: the `outputSchema` each MCP tool advertises.

Drives one scripted session through the tools, records every tool's `structuredContent`, and writes, per tool, a JSON
Schema that says: an object; these top-level keys are present in every successful result (`required`); each key has
these types (a union of what was seen). Nested objects and arrays are described by type only, and extra keys are
allowed, so the schema can only be wrong if a key listed as required is ever missing. A tool the session cannot drive
gets no schema (and is listed on stderr). `tests/unit/test_mcp_output_schemas.py` replays the same session and
validates every result against the committed file.

    .venv/bin/python tools/gen_mcp_output_schemas.py [--write]
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "raptor" / "mcp" / "output_schemas.json"

SIMPLE = {
    "system": {"term_condition": 1000, "num_runs": 3},
    "blocks": [{"id": 1, "name": "B1", "fail_dist": 4, "fail_params": [100.0, 0.0]},
               {"id": 2, "name": "B2", "fail_dist": 4, "fail_params": [100.0, 0.0]}],
    "nodes": [{"id": 10, "name": "Start", "type": "start"}, {"id": 13, "name": "S", "type": "regular", "k": 1},
              {"id": 11, "name": "J", "type": "regular", "n": 2, "k": 1}, {"id": 12, "name": "End", "type": "end"}],
    "links": [{"from": "Start", "to": "S"}, {"from": "S", "to": "B1"}, {"from": "S", "to": "B2"},
              {"from": "B1", "to": "J"}, {"from": "B2", "to": "J"}, {"from": "J", "to": "End"}],
}


def _json_type(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


class Recorder:
    def __init__(self) -> None:
        self.seen: dict[str, list[dict]] = {}
        self.failed: dict[str, str] = {}

    def call(self, _tool: str, **args):
        name = _tool
        from raptor.mcp import server
        try:
            result = asyncio.run(server.mcp.call_tool(name, args))
        except Exception as exc:                       # an argument the tool refused outright
            self.failed.setdefault(name, repr(exc)[:100])
            return None
        sc = getattr(result, "structured_content", None)
        if getattr(result, "is_error", False) or not isinstance(sc, dict):
            self.failed.setdefault(name, "error or no structured content")
            return None
        self.seen.setdefault(name, []).append(sc)
        return sc


def session(rec: Recorder, workspace: Path) -> None:  # noqa: PLR0915
    """The scripted tool session. Deterministic, a few seconds."""
    from raptor.mcp import tools_model, tools_run, tools_metrics, tools_tables, tools_view  # noqa: F401 (registers the tools)
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(workspace), "read_only": False})()
    mcp._raptor_ws = None
    shutil.copy(ROOT / "tests" / "golden" / "Phasing3" / "input.rbd", workspace / "Phasing3.rbd")
    rec.call("ping")
    rec.call("list_distributions")
    rec.call("workspace_info")
    rec.call("list_models")
    m1 = (rec.call("build_model", name="m1", spec=SIMPLE) or {}).get("model_id")
    rec.call("build_model", name="m1_unvalidated", spec=SIMPLE,
             validate_model=False)
    m2 = (rec.call("open_model", path="Phasing3.rbd") or {}).get("model_id")
    m3 = (rec.call("create_model", name="m3", template="series") or {}).get("model_id")
    if not (m1 and m2 and m3):
        return
    for m in (m1, m2):
        rec.call("describe_model", model_id=m)
        rec.call("describe_model", model_id=m, detail="full")
        rec.call("validate_model", model_id=m)
    rec.call("get_element", model_id=m1, selector="B1")
    rec.call("get_element", model_id=m1, selector="J")
    rec.call("get_element", model_id=m1, selector="Link:1")
    rec.call("update_element", model_id=m1, selector="B1", patch={"fail_params": [90.0, 0.0]})
    rec.call("configure_system", model_id=m1, num_runs=3)
    rec.call("configure_sparing", model_id=m1, selector="B1", mode="infinite")
    rec.call("configure_maintenance", model_id=m1, selector="B1", pm_freq=50.0)
    rec.call("configure_phases", model_id=m3, phases=[{"name": "P1", "duration_dist": 19, "params": [10.0, 0, 0]}])
    rec.call("manage_rates", model_id=m1, action="list")
    rec.call("manage_rates", model_id=m1, action="add", name="audit_rate",
             expr="1/1000")
    rec.call("configure_block_defaults", action="list")
    rec.call("add_block", model_id=m3, name="X1", fail_dist=19, fail_params=[10.0])
    rec.call("add_node", model_id=m3, type="regular", name="NX")
    rec.call("add_event", model_id=m3, name="E1")
    rec.call("connect", model_id=m3, from_element="X1", to_element="NX")
    rec.call("add_annotation", model_id=m1, kind="text", text="hello", x=5.0, y=5.0)
    rec.call("add_hierarchy", model_id=m3, name="H1")
    rec.call("list_annotations", model_id=m1)
    rec.call("update_annotation", model_id=m1, annotation_id=1, patch={"text": "hi"})
    rec.call("delete_annotation", model_id=m1, annotation_id=1)
    rec.call("auto_layout", model_id=m1)
    rec.call("undo_last_edit", model_id=m1)
    rec.call("redo_last_edit", model_id=m1)
    rec.call("diff_models", model_a=m1, model_b=m3)
    rec.call("diff_models", model_a=m1, model_b=m1, detail="full", ignore=[])
    rec.call("estimate_runtime", model_id=m1)
    sim =rec.call("simulate", model_id=m1, num_runs=2) or {}
    rec.call("simulate", model_id=m2, num_runs=2)
    run = sim.get("run_id")
    if run:
        rec.call("get_report", run_id=run, section="final_results")
        rec.call("get_timeseries", run_id=run, series="availability")
        rec.call("get_timeseries", run_id=run, series="availability",
                 runs="all", points=3)
        rec.call("get_report", run_id=run, section="end_of_run",
                 run_number=1)
    rec.call("describe_metrics")
    rec.call("describe_metrics", model_id=m1,
             **({"run_id": run} if run else {}))
    iso = rec.call("simulate", model_id=m2, num_runs=1,
                   confirm_long_run=True, hierarchy="500path") or {}
    if iso.get("run_id"):
        rec.call("describe_metrics", run_id=iso["run_id"])
    rec.call("single_point_failures", model_id=m1)
    rec.call("single_point_failures", model_id=m2)
    rec.call("failure_effects", model_id=m1, fail=["B1"])
    rec.call("failure_effects", model_id=m2, phase=2)
    rec.call("compare_models", models=[m1, m2], num_runs=2)
    rec.call("sweep_parameter", model_id=m1, selector="B1", field="fail_params", values=[[80.0, 0.0], [120.0, 0.0]],
             num_runs=2)
    rec.call("extract_hierarchy", model_id=m2, hierarchy="500path", report_only=True)
    rec.call("extract_hierarchy", model_id=m2, hierarchy="500path")
    rec.call("list_tables", model_id=m1)
    if run:
        rec.call("list_tables", model_id=m1, run_id=run)
    rec.call("get_table", model_id=m1, table="inputs.blocks")
    rec.call("get_table", model_id=m1, run_id=run, table="results.summary") if run else None
    rec.call("export_tables", model_id=m1, tables=["inputs.blocks"], formats=["csv"], folder=str(workspace / "tables"))
    rec.call("search_docs", query="standby")
    rec.call("read_doc", name="ch08-standby")
    rec.call("read_doc", name="rbds", limit=5)
    rec.call("read_doc", name="iec61078", limit=5)
    rec.call("save_model_as", model_id=m1, path="out.rbds")
    rec.call("delete_element", model_id=m3, selector="E1")
    rec.call("collapse_selection", model_id=m1, selectors=["B1"], name="Sub")   # one link in, one out: a hierarchy takes no more (Raptor 7)
    rec.call("close_model", model_id=m3)
    rec.call("delete_file", path="out.rbds")


#: Keys whose type depends on the data, so one scripted session cannot see every type: (tool, key) -> types.
WIDEN: dict[tuple[str, str], list[str]] = {
    ("get_report", "next_offset"): ["integer", "null"],
    ("get_table", "next_offset"): ["integer", "null"],
    ("list_models", "next_offset"): ["integer", "null"],
    ("read_doc", "next_offset"): ["integer", "null"],
}

#: Keys that only some element kinds return. `get_element` shares one
#: output schema between blocks/nodes/events/hierarchies and links; the
#: block-shaped fields cannot be required, or every link lookup is
#: rejected before the client sees it.
REQUIRED_OVERRIDE: dict[str, list[str]] = {
    "get_element": ["id", "kind"],
}


def infer(seen: dict[str, list[dict]]) -> dict[str, dict]:
    schemas = {}
    for name, results in sorted(seen.items()):
        keys = set.intersection(*(set(r) for r in results))
        props: dict[str, dict] = {}
        for r in results:
            for k, v in r.items():
                props.setdefault(k, {"type": set()})["type"].add(_json_type(v))
        schemas[name] = {
            "type": "object",
            "properties": {k: {"type": sorted(p["type"]) if len(p["type"]) > 1 else next(iter(p["type"]))}
                           for k, p in sorted(props.items())},
            "required": sorted(REQUIRED_OVERRIDE.get(name, keys)),
        }
    for (tool, key), types in WIDEN.items():
        if tool in schemas and key in schemas[tool]["properties"]:
            schemas[tool]["properties"][key] = {"type": types}
    return schemas


def main() -> int:
    record = Recorder()
    with tempfile.TemporaryDirectory() as d:
        session(record, Path(d))
    schemas = infer(record.seen)
    asyncio.set_event_loop(asyncio.new_event_loop())
    from raptor.mcp import server, tools_model, tools_run, tools_tables, tools_view  # noqa: F401
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for n in sorted(names - set(schemas)):
        print(f"no schema: {n} ({record.failed.get(n, 'not driven')})", file=sys.stderr)
    if "--write" in sys.argv:
        OUT.write_text(json.dumps(schemas, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT} ({len(schemas)} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

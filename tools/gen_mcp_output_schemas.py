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
import time
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
    from raptor.mcp.server import import_all_tool_modules
    import_all_tool_modules()
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
    rec.call("analytical_analysis", model_id=m1, at=100.0,
             ignore_repairs=True, equation=True)
    rec.call("analytical_analysis", model_id=m1, at=100.0,
             ignore_repairs=True)
    rec.call("analytical_analysis", model_id=m1, mode="availability")
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
    rec.call("render_diagram_svg", model_id=m1)
    rec.call("undo_last_edit", model_id=m1)
    rec.call("redo_last_edit", model_id=m1)
    rec.call("diff_models", model_a=m1, model_b=m3)
    rec.call("diff_models", model_a=m1, model_b=m1, detail="full", ignore=[])
    rec.call("estimate_runtime", model_id=m1)
    sim =rec.call("simulate", model_id=m1, num_runs=2,
                  availability_plot=True) or {}
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
    rec.call("list_advisor_rules")
    rec.call("advise", model_id=m1)
    attributed = rec.call("simulate", model_id=m1, num_runs=3, attribution=True) or {}
    if attributed.get("run_id"):
        rec.call("advise", model_id=m1, run_id=attributed["run_id"], min_severity="info",
                 targets=[{"metric": "system.ao", "op": ">=", "value": 0.5}])
        rec.call("downtime_drivers", run_id=attributed["run_id"])
        rec.call("annual_availability", run_id=attributed["run_id"], year_length=250.0, below=0.9)
        # without `below` the threshold is null: a schema that saw only the
        # number rejected that answer in a validating client
        rec.call("annual_availability", run_id=attributed["run_id"], year_length=250.0)
    if rec.call("apply_fix", model_id=m1, rule="M-EXP-001"):
        rec.call("undo_last_edit", model_id=m1)
    iso = rec.call("simulate", model_id=m2, num_runs=1,
                   confirm_long_run=True, hierarchy="500path") or {}
    if iso.get("run_id"):
        rec.call("describe_metrics", run_id=iso["run_id"])
    rec.call("single_point_failures", model_id=m1)
    rec.call("single_point_failures", model_id=m2)
    rec.call("failure_effects", model_id=m1, fail=["B1"])
    rec.call("failure_effects", model_id=m2, phase=2)
    rec.call("failure_effects_batch", model_id=m1,
             scenarios=[{"fail": ["B1"]}, {"fail": ["no such block"]}])
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
    more(rec, m1, run, attributed.get("run_id"))


#: One block with a spare pool, planned maintenance and a repair crew, so
#: each optimiser has something to search.
OPT_SPEC = {
    "blocks": [{"name": "Pump", "fail_dist": "Weibull", "fail_params": [2.0, 100.0, 0.0],
                "repair_dist": "Fixed", "repair_params": [5, 0, 0],
                "uses_pm": 1, "pm_freq": 400.0, "pm_dist": "Fixed", "pm_params": [2, 0, 0],
                "pm_cost": 1.0, "pm_fixed_cost": 10.0, "rep_fixed_cost": 100.0,
                "spare_pool": "Seals", "sp_type": "Pooled",
                "resource1_name": "Crew", "num_res": 1, "res1_rep": 1}],
    "spare_pools": [{"name": "Seals", "stock": 0, "rso": 1, "new_spares": 1,
                     "arrive_every": 200.0}],
    "resources": [{"name": "Crew", "num_in": 1}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"}, {"from": "Pump", "to": "End"}],
    "experiment": {"trials": 2, "termination_type": "TimeSim", "term_condition": 1000.0},
}


def optimisers(rec: Recorder) -> None:
    opt = (rec.call("build_model", name="opt", spec=OPT_SPEC) or {}).get("model_id")
    if not opt:
        return
    rec.call("optimise_spares", model_id=opt, target=0.5, max_evaluations=2, trials=2)
    rec.call("optimise_pm", model_id=opt, trials=2)
    rec.call("optimise_resources", model_id=opt, target=0.5, extra=0, trials=2)


def more(rec: Recorder, m1: str, run: str | None, attributed_run: str | None) -> None:  # noqa: PLR0915
    """The tools the main session does not reach: batch edits, library,
    pools, jobs, scenarios and run outputs. On a model of its own, so the
    main session's results do not move."""
    m4 = (rec.call("build_model", name="m4", spec=SIMPLE) or {}).get("model_id")
    if not m4:
        return
    rec.call("export_analytical", model_id=m4, folder="analytic", modes=["availability"])
    rec.call("add_elements", model_id=m4, elements=[
        {"kind": "block", "name": "B3", "fail_dist": 4, "fail_params": [100.0, 0.0]},
        {"kind": "node", "name": "N3", "type": "regular"}])
    rec.call("connect_many", model_id=m4, links=[{"from": "B3", "to": "N3"}])
    rec.call("update_elements", model_id=m4, updates=[{"selector": "B3", "patch": {"fail_params": [80.0, 0.0]}}])
    rec.call("duplicate_elements", model_id=m4, elements=["B3"], copies=1)
    rec.call("copy_element_data", model_id=m4, source="B1", targets=["B3"], groups=["Failure"])
    rec.call("replace_in_table", model_id=m4, table="inputs.blocks_all", column="blockName", find="B3_2", replace="B4")
    hier = rec.call("add_hierarchy", model_id=m4, name="H4") or {}
    hid = hier.get("hier_id")
    if hid is not None:
        rec.call("move_element", model_id=m4, selector="B4", parent=int(hid))
    rec.call("delete_elements", model_id=m4, selectors=["B4"])
    desc = rec.call("describe_model", model_id=m4, detail="full") or {}
    link = next((ln for ln in desc.get("links", []) if isinstance(ln, dict)), None)
    if link is not None:
        rec.call("delete_link", model_id=m4, link_id=str(link.get("id")))
    rec.call("delete_elements", model_id=m4, selectors=["B3", "N3"])
    for tool, extra in (("manage_resources", {"num_in": 2}), ("manage_spare_pools", {"stock": 2}),
                        ("manage_triggers", {})):
        rec.call(tool, model_id=m4, action="add", name=f"{tool}_x", **extra)
        rec.call(tool, model_id=m4, action="list")
    lib = "schema.rbdlib"
    part = ((rec.call("save_part_type", model_id=m4, element="B1", name="Unit", library_path=lib) or {})
            .get("part") or {}).get("id")
    rec.call("list_part_types", library_path=lib)
    if part:
        rec.call("apply_part_type", model_id=m4, part=part, elements=["B2"], library_path=lib)
    rec.call("sync_part_types", model_id=m4, library_path=lib)
    saved = rec.call("save_assembly", model_id=m4, elements=["B1"], name="Pair", library_path=lib) or {}
    if saved.get("part"):
        rec.call("insert_assembly", model_id=m4, part=saved["part"], library_path=lib)
    job = (rec.call("start_run", model_id=m1, num_runs=2) or {}).get("job_id")
    if job:
        end = time.monotonic() + 60
        while time.monotonic() < end:
            status = rec.call("run_status", job_id=job) or {}
            if status.get("state") not in ("queued", "running", "setting_up", "simulating", None):
                break
            time.sleep(0.2)
        rec.call("cancel_run", job_id=job)
    rec.call("render_diagram", model_id=m1, max_px=200)
    rec.call("what_if", model_id=m1, changes=["block:B1.fail_params=200,0"], trials=2)
    rec.call("sensitivity", model_id=m1, top=1, trials=2)
    if attributed_run:
        rec.call("recommend_changes", model_id=m1, run_id=attributed_run, top=1, trials=2)
        rec.call("run_story", run_id=attributed_run)
    optimisers(rec)
    if run:
        rec.call("get_run_results", run_id=run)
        rec.call("run_chart", run_id=run)
        rec.call("export_report", run_id=run, path="report.html")
        rec.call("export_tables", model_id=m1, run_id=run, tables=["results.summary"], formats=["csv"],
                 folder="tables2")


#: Keys whose type depends on the data, so one scripted session cannot see every type: (tool, key) -> types.
WIDEN: dict[tuple[str, str], list[str]] = {
    ("get_report", "next_offset"): ["integer", "null"],
    ("get_table", "next_offset"): ["integer", "null"],
    ("list_models", "next_offset"): ["integer", "null"],
    ("read_doc", "next_offset"): ["integer", "null"],
    ("analytical_analysis", "time"): ["number", "null"],
    ("analytical_analysis", "reliability"): ["number", "null"],
    ("analytical_analysis", "pdf"): ["number", "null"],
    ("analytical_analysis", "hazard"): ["number", "null"],
    ("analytical_analysis", "mttf"): ["number", "null"],
    ("analytical_analysis", "availability"): ["number", "null"],
    ("analytical_analysis", "unavailability"): ["number", "null"],
    ("analytical_analysis", "equation"): ["string", "null"],
    ("single_point_failures", "next_offset"): ["integer", "null"],
    # a scenario run reproduces the engine's fixed seeds when none is given
    ("what_if", "seed"): ["integer", "null"],
    ("sensitivity", "seed"): ["integer", "null"],
    ("recommend_changes", "seed"): ["integer", "null"],
    ("optimise_pm", "trials"): ["integer", "null"],
    ("optimise_resources", "trials"): ["integer", "null"],
    ("render_diagram", "highlighted"): ["integer", "null"],
    # a run's summary.json may lack a field an older run did not write
    ("get_run_results", "model_id"): ["string", "null"],
    ("get_run_results", "model_name"): ["string", "null"],
    ("get_run_results", "runs"): ["integer", "null"],
    ("get_run_results", "units"): ["string", "null"],
}

#: A background job's snapshot: these fields are null until the job reaches
#: the state that fills them (set-up, attribution, the finished run, an error).
for _tool in ("start_run", "run_status", "cancel_run"):
    WIDEN.update({
        (_tool, "setup"): ["object", "null"],
        (_tool, "attributing"): ["object", "null"],
        (_tool, "percent"): ["number", "null"],
        (_tool, "wall_seconds"): ["number", "null"],
        (_tool, "run_id"): ["string", "null"],
        (_tool, "summary"): ["object", "null"],
        (_tool, "error"): ["string", "null"],
    })

#: Keys a tool returns only when asked: never required.
OPTIONAL: dict[str, set[str]] = {
    "run_chart": {"svg"},            # only with render_svg
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
            "required": sorted(set(REQUIRED_OVERRIDE.get(name, keys)) - OPTIONAL.get(name, set())),
        }
    if "analytical_analysis" in schemas:
        # The tool only returns `hierarchy` for a hierarchy-scoped solve,
        # and the scripted session runs whole-model cases. Advertise it so
        # a scoped result does not silently outgrow its schema.
        schemas["analytical_analysis"]["properties"].setdefault(
            "hierarchy", {"type": ["string", "null"]})
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
    from raptor.mcp import server  # noqa: F401
    server.import_all_tool_modules()
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for n in sorted(names - set(schemas)):
        print(f"no schema: {n} ({record.failed.get(n, 'not driven')})", file=sys.stderr)
    if "--write" in sys.argv:
        OUT.write_text(json.dumps(schemas, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT} ({len(schemas)} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""MCP part-type tools: save, list, apply, sync."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

SPEC = {
    "experiment": {"trials": 2, "termination_type": "TimeSim",
                   "term_condition": 100.0},
    "blocks": [{"name": "Pump", "fail_dist": "Fixed",
                "fail_params": [20, 0, 0], "repair_dist": "Fixed",
                "repair_params": [2, 0, 0]}],
    "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
    "links": [{"from": "Start", "to": "Pump"},
              {"from": "Pump", "to": "End"}],
}


@pytest.fixture
def server(tmp_path):
    from raptor.mcp import server as srv
    from raptor.mcp import tools_advisor, tools_library, tools_model, tools_run  # noqa: F401
    srv.mcp._raptor_args = type("A", (), {"workspace": str(tmp_path),
                                          "read_only": False})()
    srv.mcp._raptor_ws = None
    yield srv
    srv.mcp._raptor_ws = None


def _call(server, tool, **args):
    return asyncio.run(server.mcp.call_tool(tool, args))


def _ok(result):
    text = " ".join(c.text for c in result.content
                    if getattr(c, "type", "") == "text")
    assert not result.is_error, text
    return result.structured_content


@pytest.fixture
def model(server):
    return _ok(_call(server, "build_model", name="m", spec=SPEC,
                     validate_model=False))["model_id"]


def test_save_list_apply_and_sync(server, model, tmp_path):
    library = tmp_path / "site.rbdlib"
    saved = _ok(_call(server, "save_part_type", model_id=model,
                      element="Pump", name="Pump, centrifugal",
                      library_path=library.name))
    part = saved["part"]["id"]
    assert saved["linked"] is True
    listed = _ok(_call(server, "list_part_types",
                       library_path=library.name))
    assert listed["count"] == 1 and listed["parts"][0]["id"] == part
    model_parts = _ok(_call(server, "list_part_types", model_id=model))
    assert model_parts["count"] == 1

    applied = _ok(_call(server, "apply_part_type", model_id=model,
                        part=part, elements=["Pump"],
                        library_path=library.name))
    assert applied["count"] == 1
    synced = _ok(_call(server, "sync_part_types", model_id=model,
                       library_path=library.name))
    assert synced["dry_run"] is True and synced["count"] == 0


def test_duplicate_and_paste_data_through_mcp(server, model):
    duplicated = _ok(_call(server, "duplicate_elements", model_id=model,
                           elements=["Pump"], copies=1))
    assert duplicated["count"] == 1
    copied = _ok(_call(server, "copy_element_data", model_id=model,
                       source="Pump", targets=["Pump_2"],
                       groups=["Failure"]))
    assert copied["blocks"] == 1


def test_an_assembly_saves_lists_and_inserts_a_fresh_copy(server, model, tmp_path):
    library = tmp_path / "site.rbdlib"
    saved = _ok(_call(server, "save_assembly", model_id=model,
                      elements=["Pump"], name="Pump set",
                      library_path=library.name))
    assert saved["contents"] == "1 block"
    listed = _ok(_call(server, "list_part_types", library_path=library.name))
    assert listed["parts"][0]["kind"] == "assembly"
    inserted = _ok(_call(server, "insert_assembly", model_id=model,
                         part=saved["part"], library_path=library.name))
    assert inserted["count"] == 1
    described = _ok(_call(server, "get_element", model_id=model,
                          selector="Pump_2"))
    assert described


def test_an_assembly_kept_in_the_model_inserts_by_name(server, model):
    _ok(_call(server, "save_assembly", model_id=model, elements=["Pump"],
              name="Pump set"))
    inserted = _ok(_call(server, "insert_assembly", model_id=model,
                         part="Pump set", x=300.0, y=40.0))
    assert inserted["count"] == 1


# ---- the workspace boundary and --read-only (found by MCP contract testing) --

def test_a_library_path_stays_inside_the_workspace(server, model, tmp_path):
    """A relative path is the workspace's, never the server's working
    directory; absolute and escaping paths are refused for reads and
    writes, and a file that is not a library is never echoed back."""
    _ok(_call(server, "save_part_type", model_id=model, element="Pump",
              library_path="libs/site.rbdlib"))
    assert (tmp_path / "libs" / "site.rbdlib").is_file()
    assert _ok(_call(server, "list_part_types",
                     library_path="libs/site.rbdlib"))["count"] == 1
    outside = tmp_path.parent / "outside.rbdlib"
    for bad in (str(outside), "../outside.rbdlib"):
        result = _call(server, "save_part_type", model_id=model,
                       element="Pump", library_path=bad)
        assert result.is_error
        assert not outside.exists()
    secret = tmp_path / "notes.txt"
    secret.write_text("TOP SECRET LINE", encoding="utf-8")
    for probe in ("/etc/passwd", "../" * 12 + "etc/passwd", "notes.txt"):
        result = _call(server, "list_part_types", library_path=probe)
        text = " ".join(c.text for c in result.content
                        if getattr(c, "type", "") == "text")
        assert result.is_error and "root:" not in text
        assert "TOP SECRET" not in text


def test_read_only_refuses_every_tool_that_edits(server, model):
    """Every tool not annotated read-only must call `require_write`, apart
    from the documented exceptions: running and analysing stay allowed in
    --read-only (they write run history, never a model or a user file)."""
    import importlib
    import inspect
    import pkgutil

    import raptor.mcp as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("tools_"):
            importlib.import_module(f"raptor.mcp.{info.name}")
    allowed = {"simulate", "start_run", "cancel_run", "open_model",
               "compare_models", "what_if", "sensitivity", "sweep_parameter",
               "recommend_changes", "optimise_spares", "optimise_pm",
               "optimise_resources"}
    ungated = []
    for tool in asyncio.run(server.mcp.list_tools()):
        if tool.annotations is None or tool.annotations.read_only_hint:
            continue
        fn = inspect.unwrap(server.mcp._tool_manager.get_tool(tool.name).fn)
        if "require_write" not in inspect.getsource(fn):
            ungated.append(tool.name)
    assert sorted(ungated) == sorted(allowed)

    server.mcp._raptor_args.read_only = True
    try:
        for tool, args in (
                ("duplicate_elements", {"model_id": model, "elements": ["Pump"]}),
                ("copy_element_data", {"model_id": model, "source": "Pump",
                                       "targets": ["Pump"]}),
                ("save_part_type", {"model_id": model, "element": "Pump",
                                    "library_path": "x.rbdlib"}),
                ("save_assembly", {"model_id": model, "elements": ["Pump"],
                                   "name": "a", "library_path": "x.rbdlib"})):
            result = _call(server, tool, **args)
            text = " ".join(c.text for c in result.content
                            if getattr(c, "type", "") == "text")
            assert result.is_error and "read-only" in text, (tool, text)
    finally:
        server.mcp._raptor_args.read_only = False


def test_a_part_from_a_dependent_block_applies_to_another_block(server, tmp_path):
    """A dependency on a particular element names an element of the model
    the part came from; carried into a part it made apply_part_type fail
    ("dependency percentages must add up to 100"). Like Raptor's `.rbl`
    libraries, a part keeps only a dependency on the whole system."""
    spec = {
        "experiment": {"trials": 2, "termination_type": "TimeSim",
                       "term_condition": 100.0},
        "blocks": [
            {"name": "A", "fail_dist": "Fixed", "fail_params": [20, 0, 0],
             "repair_dist": "Fixed", "repair_params": [2, 0, 0]},
            {"name": "B", "fail_dist": "Fixed", "fail_params": [30, 0, 0],
             "repair_dist": "Fixed", "repair_params": [3, 0, 0],
             "dep_type": "RBDBlock", "dep_num": 1, "dep_idle": 60,
             "dep_nothing": 40},
            {"name": "C", "fail_dist": "Fixed", "fail_params": [40, 0, 0],
             "repair_dist": "Fixed", "repair_params": [4, 0, 0]}],
        "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
        "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "B"},
                  {"from": "B", "to": "C"}, {"from": "C", "to": "End"}],
    }
    model = _ok(_call(server, "build_model", name="dep", spec=spec,
                      validate_model=False))["model_id"]
    saved = _ok(_call(server, "save_part_type", model_id=model, element="B",
                      library_path="dep.rbdlib", link=False))
    assert not any(k.startswith("dep_") for k in saved["part"]["fields"])
    _ok(_call(server, "apply_part_type", model_id=model,
              part=saved["part"]["id"], elements=["C"],
              library_path="dep.rbdlib"))
    _ok(_call(server, "validate_model", model_id=model))
    from raptor.api.library.model import PartType, applicable_fields
    old = PartType(id="p", name="p", kind="block",
                   fields={"fail_dist": 4, "dep_num": 1, "dep_type": "RBDBlock",
                           "dep_idle": 60})
    assert applicable_fields(old) == {"fail_dist": 4}
    system = PartType(id="s", name="s", kind="block",
                      fields={"dep_num": -2, "dep_type": "None", "dep_idle": 100})
    assert applicable_fields(system)["dep_num"] == -2


def test_a_library_must_be_an_rbdlib_outside_the_managed_trees(tmp_path):
    import asyncio

    from raptor.mcp import server as srv
    from raptor.mcp import tools_library, tools_model  # noqa: F401
    srv.mcp._raptor_args = type("A", (), {"workspace": str(tmp_path), "read_only": False})()
    srv.mcp._raptor_ws = None
    spec = {"blocks": [{"name": "A", "fail_params": [100, 0, 0], "repair_params": [5, 1, 0]}],
            "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
            "links": [{"from": "Start", "to": "A"}, {"from": "A", "to": "End"}]}
    res = asyncio.run(srv.mcp.call_tool("build_model", {"name": "m", "spec": spec,
                                                         "validate_model": False}))
    mid = res.structured_content["model_id"]

    def save(path, **kw):
        return asyncio.run(srv.mcp.call_tool("save_part_type", {
            "model_id": mid, "element": "A", "library_path": path, "link": False, **kw}))

    def refused(path, **kw):
        try:
            return save(path, **kw).is_error
        except Exception:  # noqa: BLE001 - a schema refusal raises
            return True
    assert refused("notes.txt")
    assert refused(f"models/{mid}/evil.rbdlib")
    assert refused("runs/x.rbdlib")
    assert refused("lib", basis="guesswork")
    ok = save("lib")
    assert not ok.is_error and (tmp_path / "lib.rbdlib").is_file()
    srv.mcp._raptor_ws = None

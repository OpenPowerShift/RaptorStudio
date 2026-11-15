"""Every MCP tool's input properties carry a description where one is known."""
from __future__ import annotations

import asyncio
import importlib
import pkgutil

from raptor.mcp.param_docs import COMMON, describe, from_docstring


def _tools():
    import raptor.mcp as package
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("tools"):
            importlib.import_module(f"raptor.mcp.{info.name}")
    from raptor.mcp import server
    return asyncio.run(server.mcp.list_tools())


def test_the_docstring_sentence_naming_the_argument_wins():
    doc = """Do a thing.

    `depth` is how far to go. Leave `model_id` out
    and nothing happens."""
    assert from_docstring(doc, "depth") == "`depth` is how far to go."
    assert from_docstring(doc, "model_id") == "Leave `model_id` out and nothing happens."
    assert from_docstring(doc, "missing") is None
    props = {"depth": {"type": "integer"}, "run_id": {"type": "string"},
             "kept": {"type": "string", "description": "already said"}, "other": {}}
    assert describe(props, doc) == 2
    assert props["run_id"]["description"] == COMMON["run_id"]
    assert props["kept"]["description"] == "already said"
    assert "description" not in props["other"]          # no guess


def test_listed_tools_describe_most_arguments_and_every_common_one():
    total = described = 0
    for tool in _tools():
        for name, prop in (tool.input_schema or {}).get("properties", {}).items():
            total += 1
            described += bool(prop.get("description"))
            if name in COMMON:
                assert prop.get("description"), (tool.name, name)
    # 309 of 437 when this was written; a drop means a docstring lost its
    # mention of an argument
    assert described / total >= 0.68, (described, total)

"""Every capability has a door on the API, the command line, the MCP server
and the GUI, or says why not."""
from __future__ import annotations

import asyncio
import importlib

import pytest

from raptor.api.capabilities import CAPABILITIES, SURFACES

IDS = [c.id for c in CAPABILITIES]


def _resolve(dotted: str):
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


def test_ids_are_unique_and_every_surface_is_accounted_for():
    assert len(set(IDS)) == len(IDS)
    for cap in CAPABILITIES:
        for surface in SURFACES:
            door = getattr(cap, surface)
            if door is None:
                assert cap.absent.get(surface, "").strip(), \
                    f"{cap.id}: no {surface} door and no reason given"
            else:
                assert surface not in cap.absent, f"{cap.id}: {surface} has a door and a reason"


@pytest.mark.parametrize("cap", CAPABILITIES, ids=IDS)
def test_api_function_exists(cap):
    assert callable(_resolve(cap.api))


@pytest.mark.parametrize("cap", [c for c in CAPABILITIES if c.cli], ids=lambda c: c.id)
def test_cli_command_and_flag_exist(cap):
    from raptor.cli.main import build_parser
    command, _, flag = cap.cli.partition(" ")
    parser = build_parser()
    subs = next(a for a in parser._actions if getattr(a, "choices", None)
                and command in a.choices)
    if flag:
        options = {o for a in subs.choices[command]._actions for o in a.option_strings}
        assert flag in options, f"raptor {command} has no {flag}"


@pytest.mark.parametrize("cap", [c for c in CAPABILITIES if c.mcp], ids=lambda c: c.id)
def test_mcp_tool_is_registered(cap):
    pytest.importorskip("mcp")
    from raptor.mcp import (
        server, tools_advisor, tools_library, tools_run,  # noqa: F401
    )
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert cap.mcp in names


@pytest.mark.parametrize("cap", [c for c in CAPABILITIES if c.gui], ids=lambda c: c.id)
def test_gui_door_exists(cap):
    kind, _, target = cap.gui.partition(":")
    if kind == "command":
        from raptor.gui.command_table import COMMANDS
        assert target in {c.id for c in COMMANDS}
    else:
        assert kind == "widget" and isinstance(_resolve(target), type)


def test_long_running_api_functions_take_progress_and_abort():
    import inspect
    for cap in CAPABILITIES:
        if cap.long_running and cap.api == "raptor.api.run.run":
            params = inspect.signature(_resolve(cap.api)).parameters
            assert "progress" in params and "abort" in params

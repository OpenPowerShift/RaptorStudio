"""U27 over MCP: set_out_of_service, describe_model's listing, the run note."""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from tests.unit.test_mcp_advisor_tools import call, ok, server  # noqa: F401 -- fixture
from tests.unit.test_mcp_advisor_tools import SPEC


@pytest.fixture
def model(server):  # noqa: F811
    return ok(call(server, "build_model", name="m", spec=SPEC, validate_model=False))["model_id"]


def test_cut_lists_runs_and_restores(server, model):  # noqa: F811
    data = ok(call(server, "set_out_of_service", model_id=model, elements=["Pump"], state="cut"))
    assert data["changed"][0]["state"] == "cut"
    assert [o["name"] for o in data["out_of_service"]] == ["Pump"]
    described = ok(call(server, "describe_model", model_id=model))
    assert described["out_of_service"][0]["state"] == "cut"
    run = ok(call(server, "simulate", model_id=model, num_runs=3))
    assert any("Out of service: Pump (cut)" in w for w in run["warnings"])
    back = ok(call(server, "set_out_of_service", model_id=model, elements=["Pump"],
                   state="in_service"))
    assert back["out_of_service"] == []


def test_a_node_is_refused(server, model):  # noqa: F811
    result = call(server, "set_out_of_service", model_id=model, elements=["J"], state="cut")
    assert result.is_error

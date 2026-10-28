"""get_element (per-element read), dependency help and server naming."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO / "tests" / "golden"


def _use_workspace(tmp_path):
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None


@pytest.fixture
def motor(tmp_path):
    _use_workspace(tmp_path)
    from raptor.mcp.tools_model import open_model
    r = open_model("MotorDemo")
    assert not r.is_error
    return r.structured_content["model_id"]


def _get(mid, selector, detail="summary"):
    from raptor.mcp.tools_model import get_element
    r = get_element(mid, selector, detail)
    assert not r.is_error, r.content
    return r.structured_content


class TestGetElementBlock:
    def test_failure_distribution_is_decoded(self, motor):
        d = _get(motor, "Armature")
        f = d["failure"]
        assert f["name"] == "Weibull"
        assert f["params"] == {"shape": 1.75, "scale": 174.0, "location": 0.0}
        assert f["mean"] == pytest.approx(154.9675, rel=1e-5)

    def test_undefined_stdev_is_null_with_note(self, motor):
        rep = _get(motor, "Armature")["repair"]
        assert rep["name"] == "Pearson5"
        assert rep["mean"] == pytest.approx(18.0)
        assert rep["stdev"] is None
        assert any("stdev" in n for n in rep["notes"])

    def test_neighbours(self, motor):
        d = _get(motor, "Armature")
        assert [u["name"] for u in d["upstream"]] == ["Casing"]
        assert [u["name"] for u in d["downstream"]] == ["Magnets"]

    def test_selector_forms(self, motor):
        assert _get(motor, "Block:2")["name"] == "Armature"

    def test_logistics_delay_and_sparing(self, motor):
        d = _get(motor, "Armature")
        assert d["logistics_delay"]["before_repair"]["params"] == {"value": 5.0}
        assert d["sparing"]["type"] == "Custom"
        assert d["sparing"]["stock"] == 2
        assert d["maintenance"] == {"enabled": False}

    def test_full_adds_costs(self, motor):
        assert "costs" not in _get(motor, "Armature")
        full = _get(motor, "Armature", "full")
        assert full["costs"]["repair_fixed"] if False else full["costs"]["rep_fixed"] == 3.5

    def test_infinite_sparing_has_no_extra_fields(self, motor):
        # PowerSupply etc. are Custom; Infinite is the template default
        d = _get(motor, "PowerSupply")
        assert d["failure"]["name"] == "Triangular"
        assert d["failure"]["mean"] == pytest.approx(37.3333, rel=1e-4)


class TestOtherKinds:
    def test_node(self, motor):
        d = _get(motor, "start")
        assert d["kind"] == "Node" and d["node_type"] == "start"
        assert d["downstream"][0]["name"] == "Casing"

    def test_link(self, motor):
        d = _get(motor, "Link:2")
        assert d["from"]["name"] == "Casing" and d["to"]["name"] == "Armature"
        assert d["nom_flow"] == -2

    def test_unknown_is_not_found(self, motor):
        from raptor.mcp.tools_model import get_element
        r = get_element(motor, "Nonesuch")
        assert r.is_error
        assert r.structured_content["error"]["code"] == "not_found"


class TestDependencyHelp:
    def test_armature_depends_on_powersupply(self, motor):
        dep = _get(motor, "Armature")["dependency"]
        assert dep["dependent"] is True
        assert dep["follows"] == {"kind": "block", "id": 5, "name": "PowerSupply"}
        assert dep["effects_percent"]["idle"] == 100.0
        assert "PowerSupply" in dep["meaning"] and "Idle" in dep["meaning"]

    def test_independent_block(self, motor):
        dep = _get(motor, "Casing")["dependency"]
        assert dep == {"dependent": False, "meaning": dep["meaning"]}
        assert "Independent" in dep["meaning"]

    def test_set_read_clear_round_trip(self, motor):
        from raptor.mcp.tools_model import update_element
        patch = {"dep_type": "RBDBlock", "dep_num": 5, "dep_nothing": 0,
                 "dep_idle": 60, "dep_pm": 0, "dep_fail": 40}
        assert not update_element(motor, "Magnets", patch).is_error
        dep = _get(motor, "Magnets")["dependency"]
        assert dep["effects_percent"] == {"nothing": 0.0, "idle": 60.0,
                                          "pm": 0.0, "failure": 40.0}
        assert "induced to fail" in dep["meaning"]

        assert not update_element(motor, "Magnets", {"dep_num": -2}).is_error
        assert _get(motor, "Magnets")["dependency"]["follows"]["kind"] == "system"
        assert not update_element(motor, "Magnets", {"dep_num": -1}).is_error
        assert _get(motor, "Magnets")["dependency"]["follows"]["kind"] == "local"

        assert not update_element(
            motor, "Magnets", {"dep_type": "None", "dep_num": 0}).is_error
        assert _get(motor, "Magnets")["dependency"]["dependent"] is False

    def test_dependency_chapter_is_readable_by_name(self):
        from raptor.mcp.docs import read_doc
        assert "Backup That Wasn't" in read_doc("dependency")["text"]

    def test_docs_explain_the_fields(self):
        from raptor.mcp.docs import read_doc, search_chapters
        assert "dep_num" in read_doc("rbds")["text"]
        hits = search_chapters("dependency dep_num", limit=3)
        assert hits, "dependency fields should be findable via search_docs"


class TestNamingAndTone:
    def test_server_identity(self):
        from raptor.mcp.server import mcp
        assert mcp.name == "raptor-studio"  # identifier: kebab-case
        assert mcp.title == "Raptor Studio"
        assert "Dependencies" in mcp.instructions

    def test_get_element_is_registered_read_only(self):
        from raptor.mcp import server, tools_model  # noqa: F401
        tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        assert tools["get_element"].annotations.read_only_hint is True

    def test_no_legacy_wording_in_tool_text_or_output(self, motor):
        from raptor.mcp import server, tools_model, tools_run  # noqa: F401
        for t in asyncio.run(server.mcp.list_tools()):
            blob = json.dumps([t.description, t.input_schema]).lower()
            assert "legacy" not in blob, t.name
        for sel in ("Armature", "start", "Link:2"):
            assert "legacy" not in json.dumps(_get(motor, sel, "full")).lower()

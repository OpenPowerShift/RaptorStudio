"""The analytical solver can assume every dependency is up or down."""
from __future__ import annotations

import math
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from raptor.api.analytical import analyse_rbd, scope_options
from raptor.api.build import build_from_spec
from raptor.core.analytical import build, evaluate
from raptor.core.analytical.errors import NotApplicable
from raptor.core.engine.load import load_model
from raptor.core.fileio.rbds import load_rbds
from raptor.core.rng import Distribution

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "new_docs" / "models"

EXPONENTIAL = int(Distribution.EXPONENTIAL)
MEAN_LIFE = 200.0
MISSION = 50.0
SURVIVAL = math.exp(-MISSION / MEAN_LIFE)

START = {"name": "Start", "id": 1, "type": 1, "n": 0, "out": 1}
END = {"name": "End", "id": 3, "type": 3, "n": 1, "out": 0}


def _block(name: str, *, dep_num: int = 0, dep_type: str = "None") -> dict:
    block = {"name": name, "fail_dist": EXPONENTIAL,
             "fail_params": [MEAN_LIFE, 0.0, 0.0]}
    if dep_num:
        block.update(dep_num=dep_num, dep_type=dep_type)
    return block


def _spec(blocks: list[dict], nodes: list[dict], links: list[dict]):
    return build_from_spec(
        {"system": {"run_length": 100.0, "num_runs": 1,
                    "termination": "TimeSim", "units": "hours"},
         "blocks": blocks, "nodes": nodes, "links": links},
        auto_layout=False)


def _parallel(*, b_depends: bool = True):
    """Start -> (A || B) -> End, with B optionally dependent on A."""
    return _spec(
        [_block("A"),
         _block("B", dep_num=1, dep_type="RBDBlock") if b_depends
         else _block("B")],
        [START, END],
        [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
         {"from": "A", "to": "End"}, {"from": "B", "to": "End"}])


def _node_dependency():
    """Start -> A -> J -> End, where the junction J depends on End (node 3)."""
    junction = {"name": "J", "id": 2, "type": 2, "n": 1, "out": 1,
                "dep_num": 3, "dep_type": "RBDNode"}
    return _spec(
        [_block("A")], [START, junction, END],
        [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
         {"from": "J", "to": "End"}])


def _result(rbd, choice, *, mode="reliability", at=MISSION):
    return analyse_rbd(rbd, "m", mode=mode, at=at, ignore_repairs=True,
                       dependencies=choice, importance=False)


# --- refusal and choice -----------------------------------------------------

def test_dependency_is_refused_without_a_choice():
    with pytest.raises(NotApplicable, match="Choose whether dependencies"):
        _result(_parallel(), None)


def test_dependency_choice_still_solves():
    for choice in ("up", "down"):
        result = _result(_parallel(), choice)
        assert result["scope"]["dependencies"] == choice
        assert result["reliability"] is not None
        assert any("dependencies are treated as" in note
                   for note in result["assumptions"])


def test_up_and_down_are_both_meaningful():
    up = _result(_parallel(), "up")["reliability"]
    down = _result(_parallel(), "down")["reliability"]
    assert up == pytest.approx(1.0 - (1.0 - SURVIVAL) ** 2)
    assert down == pytest.approx(SURVIVAL)
    assert 0.0 < down < up < 1.0


def test_a_model_without_dependencies_is_unaffected_by_the_choice():
    plain = _parallel(b_depends=False)
    baseline = _result(plain, None)["reliability"]
    for choice in ("up", "down"):
        assert _result(plain, choice)["reliability"] == pytest.approx(baseline)


# --- node and link dependencies --------------------------------------------

def test_a_node_dependency_is_cut_only_when_down():
    rbd = _node_dependency()
    up = _result(rbd, "up")["reliability"]
    down = _result(rbd, "down")["reliability"]
    assert up == pytest.approx(SURVIVAL)
    assert down == 0.0
    note = next(note for note in _result(rbd, "down")["assumptions"]
                if "dependencies are treated" in note)
    assert "1 dependent block(s)/node(s)" in note   # the node, not a block


def test_a_link_dependency_choice_does_not_bypass_the_standby_gate():
    model = load_model(load_rbds(MODELS / "Bakery1.rbds"))
    for choice in ("up", "down"):
        with pytest.raises(NotApplicable, match="cold standby"):
            build(model, ignore=["repairs", "pm", "spares", "resources"],
                  dependencies=choice)


def test_a_standby_link_is_still_refused_under_a_dependency_choice():
    model = load_model(load_rbds(MODELS / "Lantern1.rbds"))
    model.links_by_id[next(iter(model.links_by_id))].sb_link = True
    with pytest.raises(NotApplicable, match="cold-standby link"):
        build(model, ignore_repairs=True, dependencies="up")


def test_standby_stress_is_not_waived_for_an_unrelated_block():
    model = load_model(load_rbds(MODELS / "Lantern1.rbds"))
    model.blocks_by_id[next(iter(model.blocks_by_id))].sb_stress = 1.8
    with pytest.raises(NotApplicable, match="standby stress"):
        build(model, ignore_repairs=True, dependencies="up")


def test_a_dependency_choice_does_not_waive_pm():
    model = load_model(load_rbds(MODELS / "Lantern1.rbds"))
    model.blocks_by_id[next(iter(model.blocks_by_id))].uses_pm = True
    with pytest.raises(NotApplicable, match="preventive maintenance"):
        build(model, ignore_repairs=True, dependencies="down")


def test_having_dependents_is_not_itself_a_dependency():
    """`has_dep_objects` marks elements others depend on; it must not make
    the element itself subject to the dependency choice."""
    rbd = _parallel(b_depends=False)
    model = load_model(rbd)
    for block in model.blocks_by_id.values():
        block.has_dep_objects = True
    baseline = analyse_rbd(rbd, "m", at=MISSION, ignore_repairs=True,
                           importance=False)["reliability"]
    for choice in ("up", "down"):
        with_dependents = build(model, ignore_repairs=True,
                                dependencies=choice)
        assert evaluate.system_probability(with_dependents, MISSION) \
            == pytest.approx(baseline)


def test_a_node_own_node_dependency_is_inert():
    """`-1` is the block-only own-node target; the engine resolves it to
    `None` for a node, so it must neither refuse nor cut the model."""
    rbd = _parallel(b_depends=False)
    model = load_model(rbd)
    junction = next(node for node in model.nodes_by_id.values()
                    if node.type_node == 3)
    junction.dependency_num = -1
    junction.dep_type = "None"
    baseline = analyse_rbd(rbd, "m", at=MISSION, ignore_repairs=True,
                           importance=False)["reliability"]
    for choice in (None, "up", "down"):
        built = build(model, ignore_repairs=True, dependencies=choice)
        assert evaluate.system_probability(built, MISSION) \
            == pytest.approx(baseline)


def test_a_phase_cut_is_not_hidden_by_a_dependency_choice():
    from raptor.core.model import Phase

    rbd = _parallel()
    model = load_model(rbd)
    model.phases = [Phase(phase_name="P")]
    dependent = model.blocks_by_id[2]          # B, the dependent block
    dependent.uses_phasing = True
    dependent.phase_type = ["C"]
    dependent.phase_value = [1.0]
    for choice in ("up", "down"):
        built = build(model, ignore_repairs=True, phase=1,
                      dependencies=choice)
        info = built.by_key[("block", 2)]
        assert info.forced_down
        assert info.state == "cut in this phase"


# --- reliability versus availability ---------------------------------------

def test_down_is_never_better_in_reliability_or_availability():
    rbd = _parallel()
    reliability = [
        _result(rbd, choice)["reliability"] for choice in ("up", "down")]
    assert reliability[1] <= reliability[0] + 1e-12
    availability = [
        _result(rbd, choice, mode="availability")["availability"]
        for choice in ("up", "down")]
    assert 0.0 <= availability[1] < availability[0] <= 1.0
    forced = _result(rbd, "down", mode="availability")["components"]
    assert any(row["state"] == "dependency forced down"
               and row["availability"] == 0.0 for row in forced)


def test_forcing_down_also_removes_the_element_at_the_first_instant():
    result = _result(_parallel(), "down")
    assert result["components"]
    assert any(row["state"] == "dependency forced down"
               and row["reliability"] == 0.0 for row in result["components"])


# --- argument validation ----------------------------------------------------

def test_invalid_dependency_value_is_a_value_error_not_a_refusal():
    for choice in ("sideways", "UP", "down "):
        with pytest.raises(ValueError, match="dependencies must be one of"):
            _result(_parallel(), choice)
        with pytest.raises(ValueError, match="dependencies must be one of"):
            build(load_model(_parallel()), ignore_repairs=True,
                  dependencies=choice)


def test_invalid_standby_value_is_a_value_error_from_the_api():
    with pytest.raises(ValueError, match="standby must be one of"):
        analyse_rbd(_parallel(), "m", at=MISSION, ignore_repairs=True,
                    standby="sideways", importance=False)


# --- scope options ----------------------------------------------------------

def test_scope_options_reports_dependencies():
    offered = scope_options(_parallel())
    assert offered["has_dependencies"] is True
    assert scope_options(_parallel(b_depends=False))["has_dependencies"] is False
    # A node dependency alone still offers the choice.
    assert scope_options(_node_dependency())["has_dependencies"] is True


# --- scale and cycles -------------------------------------------------------

def test_a_large_series_with_one_dependent_block_still_builds():
    blocks = [_block(f"B{i}", dep_num=1, dep_type="RBDBlock")
              if i == 150 else _block(f"B{i}") for i in range(300)]
    nodes = [{"name": "Start", "id": 1, "type": 1, "n": 0, "out": 1}]
    nodes += [{"name": f"N{i}", "id": 100 + i, "type": 2, "n": 1, "out": 1}
              for i in range(299)]
    nodes += [{"name": "End", "id": 3, "type": 3, "n": 1, "out": 0}]
    links = [{"from": "Start", "to": "B0"}]
    for i in range(299):
        links += [{"from": f"B{i}", "to": f"N{i}"},
                  {"from": f"N{i}", "to": f"B{i + 1}"}]
    links.append({"from": "B299", "to": "End"})
    rbd = _spec(blocks, nodes, links)
    up = _result(rbd, "up")["reliability"]
    down = _result(rbd, "down")["reliability"]
    # The dependent block is in series, so forcing it down takes the whole
    # system down; the large graph still builds without recursion trouble.
    assert 0.0 < up <= 1.0
    assert down == 0.0


def test_a_cycle_through_a_forced_down_node_is_not_walked():
    """`down` cuts the dependent junction before the cycle check, so the
    cycle is irrelevant; `up` must still refuse rather than recurse."""
    junction = {"name": "J", "id": 2, "type": 2, "n": 1, "out": 1,
                "dep_num": 3, "dep_type": "RBDNode"}
    rbd = _spec(
        [_block("A"), _block("B")], [START, junction, END],
        [{"from": "Start", "to": "A"}, {"from": "A", "to": "J"},
         {"from": "Start", "to": "B"}, {"from": "B", "to": "J"},
         {"from": "J", "to": "End"}, {"from": "End", "to": "J"}])
    model = load_model(rbd)
    cut = build(model, ignore_repairs=True, dependencies="down")
    assert cut.root == 0
    with pytest.raises(NotApplicable, match="cycle"):
        build(model, ignore_repairs=True, dependencies="up")


# --- MCP --------------------------------------------------------------------

def _mcp_workspace(tmp_path: Path):
    from raptor.mcp import context, server
    workspace = tmp_path / "ws"
    workspace.mkdir()
    shutil.copyfile(MODELS / "EspressoDep1.rbds",
                    workspace / "EspressoDep1.rbds")
    args = SimpleNamespace(workspace=str(workspace), retain_days=30,
                           read_only=False, max_runs=5000,
                           allow_write=[], allow_read=[])
    server.mcp._raptor_args = args
    server.mcp._raptor_ws = None
    context.workspace()
    return workspace


def test_mcp_accepts_and_reports_a_dependency_choice(tmp_path):
    pytest.importorskip("mcp")
    import asyncio
    from raptor.mcp.tools_analytic import analytical_analysis
    from raptor.mcp.tools_model import open_model

    _mcp_workspace(tmp_path)
    opened = open_model("EspressoDep1.rbds")
    assert not opened.is_error, opened.content
    model_id = opened.structured_content["model_id"]

    result = asyncio.run(analytical_analysis(
        model_id, at=100.0, ignore_repairs=True, dependencies="down"))
    assert not result.is_error, result.content
    assert result.structured_content["scope"]["dependencies"] == "down"
    text = result.content[0].text
    assert "dependencies down" in text
    assert "Traceback" not in text


def test_mcp_rejects_a_malformed_dependency_without_a_traceback(tmp_path):
    pytest.importorskip("mcp")
    import asyncio
    from raptor.mcp.tools_analytic import analytical_analysis
    from raptor.mcp.tools_model import open_model

    _mcp_workspace(tmp_path)
    model_id = open_model("EspressoDep1.rbds").structured_content["model_id"]
    result = asyncio.run(analytical_analysis(
        model_id, at=100.0, ignore_repairs=True, dependencies="sideways"))
    assert result.is_error
    assert result.structured_content["error"]["code"] == "invalid_argument"
    text = result.content[0].text
    assert "dependencies must be one of" in text
    assert "Traceback" not in text


def test_mcp_markdown_does_not_echo_unknown_scope_strings():
    pytest.importorskip("mcp")
    from raptor.mcp.tools_analytic import _markdown

    text = _markdown({
        "model": "m", "mode": "reliability", "time": 1.0,
        "reliability": 1.0, "scope": {
            "dependencies": "<script>alert(1)</script>",
            "standby": "javascript:1",
            "phase": None,
        },
        "importance": [], "warnings": [],
    })
    assert "<script>" not in text
    assert "javascript:" not in text

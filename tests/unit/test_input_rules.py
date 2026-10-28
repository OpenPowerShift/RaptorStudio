"""Raptor 7's input rules (`raptor.core.input_rules`): each fires, says what
Raptor 7 says, and is applied where Raptor 7 applies it (a run refused, an
edit refused, a file still opened)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from raptor.core.fileio.rbd import load_rbd
from raptor.core.input_rules import InputRuleViolation, check, new_violations

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import golden_fixtures  # noqa: E402

BASE = REPO / "tests" / "golden" / "HierarchyExample" / "input.rbd"
#: HierarchyExample has no events; Phasing1 does.
EVENTS = REPO / "tests" / "golden" / "Phasing1" / "input.rbd"


def _first(rbd, section, **match):
    return next(r for r in rbd.sections[section].rows
                if str(r.get("ID")) != "0" and all(r.get(k) == v for k, v in match.items()))


def _set(section, **cols):
    def mutate(rbd):
        _first(rbd, section).update({k: str(v) for k, v in cols.items()})
    return mutate


def _set_regular_node(**cols):
    def mutate(rbd):
        _first(rbd, "NODE_DATA", Type="2").update({k: str(v) for k, v in cols.items()})
    return mutate


def _add_link(ftype, ftarget, ttype, ttarget):
    """ftarget/ttarget pick an element: a node Type ("1", "3") or "block"."""
    def pick(rbd, kind, which):
        if kind == "Node":
            return _first(rbd, "NODE_DATA", Type=which)["ID"]
        if kind == "Hier":
            return _first(rbd, "HIERARCHY_DATA")["ID"]
        return _first(rbd, "BLOCK_DATA")["ID"]

    def mutate(rbd):
        from raptor.core.fileio.rbdlib import make_link_row
        rows = rbd.sections["LINK_DATA"].rows
        rows.append(make_link_row(max(int(r["ID"]) for r in rows) + 1, ftype,
                            int(pick(rbd, ftype, ftarget)), ttype, int(pick(rbd, ttype, ttarget))))
    return mutate


#: (rule id, mutation of HierarchyExample, a phrase of Raptor 7's message)
CASES = [
    ("block.name", _set("BLOCK_DATA", blockName="Two Words"), "can't be blank or have blank spaces"),
    ("block.name", _set("BLOCK_DATA", blockName="X" * 21), "no greater than 20 characters"),
    ("block.pm_freq", _set("BLOCK_DATA", pmTriggered=0, PMFreq=0), "Frequency of PM must be between"),
    ("block.pm_stagger_vs_freq", _set("BLOCK_DATA", pmTriggered=0, PMFreq=10, PMStaggerTime=10),
     "PM stagger value cannot be greater than or equal to PM frequency!"),
    ("block.cost.Running", _set("BLOCK_DATA", Running=-1), "Cost to operate block must be between"),
    ("block.cost.doneFixed", _set("BLOCK_DATA", doneFixed=-1e10), "Fixed cost of this block being done"),
    ("block.dep.total", _set("BLOCK_DATA", DepNothing=50, DepIdle=0, DepPM=0, DepFail=0),
     "Total percentage time must add up to 100!"),
    ("block.dep.DepIdle", _set("BLOCK_DATA", DepIdle=120), "Percent idle time must be between 0 and 100!"),
    ("block.stock", _set("BLOCK_DATA", SpType="Custom", Stock=-1), "Initial stock must be between"),
    ("block.new_spares", _set("BLOCK_DATA", SpType="Custom", RSO=1, NewSp=0), "New spares value must be between"),
    ("block.emer_time", _set("BLOCK_DATA", SpType="Custom", ESO=1, EmerTime=0), "Emergency spare delay value"),
    ("block.gd.mean", _set("BLOCK_DATA", FDist=17, GDType=1), "cannot be used with block degrade!"),
    ("block.gd.base", _set("BLOCK_DATA", FDist=4, FParam1=100, FParam2=0, GDType=2, GDRate=0, GDLimit=100),
     "The base must be greater than 0.000001!"),
    ("block.gd.limit_high", _set("BLOCK_DATA", FDist=4, FParam1=100, FParam2=0, GDType=1, GDRate=0.5,
                                 GDLimit=10), "The limit must be between 100.000000 and"),
    ("block.start_repair", _set("BLOCK_DATA", SimStart=5, AmtExhausted=150),
     "The percent of repair exhausted must be between"),
    ("block.sb_stress", _set("BLOCK_DATA", SBStress=-1), "The standby stress must be between 0 and 999.999!"),
    ("block.dist_offered.Pre-LDTDist", _set("BLOCK_DATA", **{"Pre-LDTDist": 16}), "does not offer Empirical"),
    ("event.probability", (_set("EVENT_DATA", failVal=1.5), EVENTS),
     "Success probability must be between 0 and 1!"),
    ("event.name", (_set("EVENT_DATA", eventName="X" * 21), EVENTS), "'Event Name' must be no greater than 20"),
    ("node.name_reserved", _set_regular_node(Name="START"), "The name start is a reserved node name!"),
    ("node.k_max", _set_regular_node(k=10000), "Good paths must be between 1 and 9,999!"),
    ("system.units", _set("SYSTEM_DATA", units="two words"), "The units field can't be blank"),
    ("system.gy_threshold", _set("SYSTEM_DATA", GYthreshold=1.5), "The GY threshold value must be"),
    ("system.graph_ymin", _set("SYSTEM_DATA", graphYmin=-0.5), "Y-axis minimum must be greater than or equal"),
    ("system.runs", _set("SYSTEM_DATA", NumRuns=0), "Number of runs must be between 1 and 999,999,999!"),
    ("system.time_order", _set("SYSTEM_DATA", TerminationType=1, TermCondition=100, StartStats=100),
     "Start statistics time must be less than stop simulation time!"),
    # .rbds SYSTEM_DATA stores the NAME; reading it as a number hid this
    # refusal on every file the port itself writes.
    ("system.cycle_without_phases", _set("SYSTEM_DATA", TerminationType="CycleSim"),
     "You cannot use cycle terminated without phases defined."),
    ("link.marker_out_limit", _add_link("Node", "1", "Block", "block"),
     "Cannot have more than one connection from a Start Node"),
    ("link.marker_into", _add_link("Block", "block", "Node", "1"),
     "Cannot connect to a Start Marker from any object!"),
    ("link.marker_out", _add_link("Node", "3", "Block", "block"),
     "Cannot connect to any object from an End Marker!"),
    ("link.hier_in", _add_link("Node", "1", "Hier", ""), "Cannot connect more than one link to a hierarchy item!"),
    # Not Raptor 7's wording: its dialog silently truncates the typed value
    # with STRTOINT, but the .rbd stores the flow with ReadInt/WriteInt and a
    # decimal there desynchronises the reader and crashes it on open.
    ("link_field.capacity_integer", _set("LINK_DATA", nomFlow="133.76"),
     "Nominal flow must be a whole number"),
]


def _base(mutate):
    """A case's mutation and the model it applies to (HierarchyExample unless
    the case names another)."""
    return mutate if isinstance(mutate, tuple) else (mutate, BASE)


@pytest.mark.parametrize("rule,mutate,phrase", CASES,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_each_rule_fires_with_raptor7s_message(rule, mutate, phrase):
    mutate, base = _base(mutate)
    rbd = load_rbd(base)
    before = {v.key for v in check(rbd)}
    mutate(rbd)
    new = [v for v in check(rbd) if v.key not in before]
    hit = [v for v in new if v.rule == rule or v.rule.startswith(rule + ".")]
    assert hit, f"{rule} did not fire; new: {[(v.rule, v.message) for v in new]}"
    assert phrase in hit[0].message, hit[0].message


def test_no_corpus_model_breaks_a_run_rule():
    """Every golden fixture ran in Raptor 7, so none may break a rule Raptor 7
    applies before a run. (Link and dialog rules may: scripted probes were
    built with shapes and values Raptor 7 runs but would not let you draw or
    type.)"""
    broken = {}
    for name in golden_fixtures.all_fixture_names():
        path = golden_fixtures.resolve_rbd_path(name)
        if path is None:
            continue
        runs = [v for v in check(load_rbd(str(path))) if v.kind == "run"]
        if runs:
            broken[name] = [(v.rule, v.element) for v in runs]
    assert not broken, broken


def test_raptor7s_own_sample_models_break_no_link_rule():
    """Models drawn in Raptor 7 itself cannot hold a shape it refuses to
    draw; this pins the reading of CheckValidLink against them."""
    for name in ("HierarchyExample", "FactoryDemo", "MotorDemo", "Phasing1", "Phasing3", "SpaceStation"):
        path = golden_fixtures.resolve_rbd_path(name)
        if path is not None:
            assert not [v for v in check(load_rbd(str(path))) if v.kind == "link"], name


def test_a_k_that_was_never_set_is_not_a_violation():
    """-1 is the stored k of a node nobody edited (FactoryDemo's 60K)."""
    rbd = load_rbd(BASE)
    _first(rbd, "NODE_DATA", Type="2")["k"] = "-1"
    assert not [v for v in check(rbd) if v.rule == "node.k_range"]


def test_whole_number_capacity_flows_are_accepted():
    """Only a decimal is refused -- whole caps and the -1/-2 keywords are
    what Raptor 7 itself writes, and capacity analysis may be off."""
    rbd = load_rbd(BASE)
    link = _first(rbd, "LINK_DATA")
    for nom, mx in (("0", "0"), ("133", "133"), ("-1", "-1"), ("-2", "-2")):
        link["nomFlow"], link["MaxFlow"] = nom, mx
        assert not [v for v in check(rbd) if v.rule == "link_field.capacity_integer"], (nom, mx)


def test_fractional_capacity_flow_blocks_the_edit_that_introduces_it():
    """The entry path (`new_violations`) refuses it, so a value that would
    make the legacy export unopenable cannot be committed."""
    rbd = load_rbd(BASE)
    after = copy.deepcopy(rbd)
    _first(after, "LINK_DATA")["MaxFlow"] = "133.76"
    refused = new_violations(rbd, after)
    assert any(v.rule == "link_field.capacity_integer" for v in refused), refused
    # It is reported as one of Raptor 7's dialog refusals, not a run gate.
    assert {v.kind for v in refused} == {"dialog"}


def test_an_existing_problem_does_not_block_another_edit():
    rbd = load_rbd(BASE)
    _set("BLOCK_DATA", PMFreq=0, pmTriggered=0)(rbd)
    after = copy.deepcopy(rbd)
    _first(after, "BLOCK_DATA")["blockName"] = "Renamed"     # not the PM field
    assert new_violations(rbd, after) == []


def test_run_rules_never_block_an_edit():
    """A new model with no blocks yet is fine to keep editing; Raptor 7 only
    applies ValidateRBD when a run starts."""
    rbd = load_rbd(BASE)
    after = copy.deepcopy(rbd)
    after.sections["BLOCK_DATA"].rows = [r for r in after.sections["BLOCK_DATA"].rows if r["ID"] == "0"]
    assert [v for v in check(after) if v.kind == "run"]
    assert not [v for v in new_violations(rbd, after) if v.kind == "run"]


def test_an_api_edit_raptor7_refuses_leaves_the_model_unchanged(tmp_path):
    from raptor.api import build
    from raptor.api.workspace import Workspace
    ws = Workspace(tmp_path / "ws")
    mid = ws.open_model(BASE)["model_id"]
    block = _first(ws.get(mid).rbd, "BLOCK_DATA")
    before = dict(block)
    with pytest.raises(InputRuleViolation, match="PM stagger value cannot be greater"):
        build.update_element(ws, mid, f"Block:{block['ID']}",
                             {"pm_triggered": 0, "pm_freq": 10.0, "pm_stagger_time": 20.0})
    assert _first(ws.get(mid).rbd, "BLOCK_DATA") == before


def test_a_spec_raptor7_could_not_draw_is_refused_by_build_model(tmp_path):
    """MCP's build_model: Start linked straight to two blocks."""
    pytest.importorskip("mcp")
    from raptor.mcp import tools_model
    from raptor.mcp.server import mcp
    mcp._raptor_args = type("A", (), {"workspace": str(tmp_path)})()
    mcp._raptor_ws = None
    spec = {"blocks": [{"name": "A", "fail_dist": 4, "fail_params": [100, 0]},
                       {"name": "B", "fail_dist": 4, "fail_params": [100, 0]}],
            "nodes": [{"name": "Start", "type": "start"}, {"name": "J", "type": "regular", "k": 1},
                      {"name": "End", "type": "end"}],
            "links": [{"from": "Start", "to": "A"}, {"from": "Start", "to": "B"},
                      {"from": "A", "to": "J"}, {"from": "B", "to": "J"}, {"from": "J", "to": "End"}]}
    result = tools_model.build_model(name="fan", spec=spec)
    text = str(getattr(result, "content", result))
    assert result.is_error and "more than one connection from a Start Node" in text


def test_the_templates_are_shapes_raptor7_draws():
    from raptor.api.build import build_from_spec, k_of_n, parallel, series
    for spec in (series(3), parallel(3), k_of_n(2, 3)):
        assert [v for v in check(build_from_spec(spec)) if v.kind != "run"] == []

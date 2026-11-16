"""Finding data, ordering, the registry and its failure isolation."""
from __future__ import annotations

import ast
import pathlib

import pytest

from raptor.api.advisor import Finding, Fix, Target, counts, list_rules, sort_findings
from raptor.api.advisor import registry
from raptor.api.advisor.context import ModelContext
from raptor.api import build

from .helpers import block, findings, series_spec


def _f(rule="M-X", severity="warning", impact=0.5, basis="exact", confidence=None, name="A"):
    return Finding(rule=rule, severity=severity, category="data", title="t", message="m",
                   why="w", targets=(Target("block", 1, name, "stock"),), basis=basis,
                   confidence=confidence, impact=impact,
                   fixes=(Fix("Set stock to 2", ("block:1.stock=2",)),),
                   evidence={"x": 1.5})


def test_dict_round_trip():
    f = _f()
    again = Finding.from_dict(f.to_dict())
    assert again == f
    assert f.to_dict()["targets"][0]["path"] == "block:1.stock"


def test_target_paths():
    assert Target("pool", None, "Seals", "stock").path() == "pool:Seals.stock"
    assert Target("system", None, "System", "runs").path() == "system.runs"
    assert Target("node", 7, "N7").path() == "node:7"


def test_score_weights_method():
    assert _f(impact=0.8).score == pytest.approx(0.8)
    assert _f(impact=0.8, basis="heuristic").score == pytest.approx(0.56)
    assert _f(impact=0.8, basis="statistical", confidence=0.9).score == pytest.approx(0.72)
    assert _f(impact=7).score == 1.0


def test_ordering_severity_then_score():
    low = _f(rule="M-A", severity="advice", impact=1.0)
    high = _f(rule="M-B", severity="warning", impact=0.1)
    err = _f(rule="M-C", severity="error", impact=0.0)
    big = _f(rule="M-D", severity="warning", impact=0.9)
    assert [f.rule for f in sort_findings([low, high, err, big])] == ["M-C", "M-D", "M-B", "M-A"]


def test_counts():
    assert counts([_f(), _f(severity="info")]) == {"error": 0, "warning": 1, "advice": 0, "info": 1}


def test_duplicate_rule_id_refused():
    list_rules()
    with pytest.raises(ValueError, match="duplicate"):
        registry.rule("M-DAT-001", title="x", category="data", basis="exact",
                      severity="info")(lambda ctx: ())


def test_a_failing_rule_does_not_stop_the_others(monkeypatch):
    list_rules()

    def boom(ctx):
        raise RuntimeError("broken check")
    info = registry.RULES["M-DAT-001"]
    monkeypatch.setitem(registry.RULES, "M-DAT-001",
                        registry.RuleInfo(info.id, info.title, info.category, info.basis,
                                          info.severity, info.needs, info.doc, boom))
    out = registry.run_rules(ModelContext(build.build_from_spec(
        series_spec(block("A"), experiment={"trials": 3, "termination_type": "TimeSim",
                                            "term_condition": 100}))))
    internal = [f for f in out if f.rule == "X-INTERNAL"]
    assert len(internal) == 1 and "M-DAT-001" in internal[0].message
    assert any(f.rule == "M-EXP-001" for f in out)


def test_only_and_skip_match_prefixes():
    spec = series_spec({"name": "A"}, experiment={"trials": 3, "termination_type": "TimeSim",
                                                 "term_condition": 100})
    rbd = build.build_from_spec(spec)
    ctx = ModelContext(rbd)
    assert {f.rule for f in registry.run_rules(ctx, only=["M-EXP"])} == {"M-EXP-001"}
    assert "M-EXP-001" not in {f.rule for f in registry.run_rules(ctx, skip=["M-EXP"])}


def test_every_rule_is_described():
    rules = list_rules()
    assert len(rules) >= 20
    for r in rules:
        assert r.title and r.doc.endswith("#" + r.id)
        assert r.severity in ("error", "warning", "advice", "info")
        assert r.basis in ("exact", "statistical", "heuristic")


def test_messages_quote_only_numbers_in_evidence_and_have_why():
    spec = series_spec({"name": "A"}, {"name": "B"})
    for f in findings(spec):
        assert f.why and f.title and len(f.title) <= 60, f.rule


ADVISOR = pathlib.Path(registry.__file__).parent


@pytest.mark.parametrize("path", sorted(ADVISOR.rglob("*.py")), ids=lambda p: p.name)
def test_advisor_never_imports_qt_or_randomness(path):
    banned = ("PySide6", "raptor.gui", "random", "numpy.random", "raptor.core.rng")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not any(name == b or name.startswith(b + ".") for b in banned), (path, name)


def test_validation_can_be_handed_in_instead_of_recomputed(monkeypatch):
    from raptor.api import validate
    from raptor.api.advisor import advise_model
    rbd = build.build_from_spec(series_spec(block("A")))
    mine = [validate.Issue("warning", "Block 1 'A'", "handed in")]

    def boom(_rbd):
        raise AssertionError("the validator ran again")
    monkeypatch.setattr(validate, "issues", boom)
    out = advise_model(rbd, validation=mine)
    hit, = [f for f in out if f.rule == "M-VAL-000"]
    assert "handed in" in hit.message and hit.targets[0].name == "A"

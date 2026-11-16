"""Dismissed Advisor findings: kept in the model, filtered by default, restorable."""
from __future__ import annotations

import logging

from raptor.api import build
from raptor.api.advisor import advise_model, ignore
from raptor.api.advisor.finding import Finding, Target
from raptor.core.fileio.rbds import load_rbds, save_rbds

from .helpers import block, parallel_spec


def flawed():
    return build.build_from_spec(parallel_spec(
        block("A"), block("B", dep_type="RBDBlock", dep_num=1, dep_idle=100)))


B_DEP = (Target("block", 2, "B", "dep_type"),)


def finding(rule="M-STR-002", targets=B_DEP):
    return Finding(rule=rule, severity="warning", category="structure", title="t",
                   message="m", why="w", targets=targets)


def test_target_drops_the_field():
    assert ignore.target_of(finding()) == "block:2"
    assert ignore.target_of(finding(targets=())) == ""


def test_ignore_hides_the_finding_and_restore_brings_it_back():
    rbd = flawed()
    assert "M-STR-002" in {f.rule for f in advise_model(rbd)}
    target = ignore.target_of(next(f for f in advise_model(rbd) if f.rule == "M-STR-002"))
    ignore.ignore(rbd, "M-STR-002", target, "  deliberate cold standby ")
    assert "M-STR-002" not in {f.rule for f in advise_model(rbd)}
    assert "M-STR-002" in {f.rule for f in advise_model(rbd, include_ignored=True)}
    assert ignore.entries(rbd) == [
        {"rule": "M-STR-002", "target": target, "reason": "deliberate cold standby"}]
    assert ignore.restore(rbd, "M-STR-002", target) is True
    assert ignore.restore(rbd, "M-STR-002", target) is False
    assert "M-STR-002" in {f.rule for f in advise_model(rbd)}
    assert ignore.entries(rbd) == []


def test_a_whole_rule_entry_matches_every_target():
    shown, hidden = ignore.split([finding(), finding(targets=(Target("block", 5, "E"),))],
                                 type("R", (), {"advisor": {"ignored": [
                                     {"rule": "M-STR-002", "target": ""}]}})())
    assert shown == [] and len(hidden) == 2


def test_one_target_entry_leaves_the_others():
    rbd = type("R", (), {"advisor": {}})()
    ignore.ignore(rbd, "M-STR-002", "block:2")
    other = finding(targets=(Target("block", 5, "E"),))
    shown, hidden = ignore.split([finding(), other], rbd)
    assert shown == [other] and hidden == [finding()]


def test_ignoring_again_replaces_the_reason():
    rbd = type("R", (), {"advisor": {}})()
    ignore.ignore(rbd, "M-STR-002", "block:2", "first")
    ignore.ignore(rbd, "M-STR-002", "block:2", "second")
    assert [e["reason"] for e in ignore.entries(rbd)] == ["second"]


def test_parse():
    assert ignore.parse("M-DEP-001") == ("M-DEP-001", "")
    assert ignore.parse(" M-DEP-001@block:3 ") == ("M-DEP-001", "block:3")


def test_the_entries_round_trip_through_rbds_without_a_warning(tmp_path, caplog):
    rbd = flawed()
    ignore.ignore(rbd, "M-STR-002", "block:2", "deliberate")
    path = tmp_path / "m.rbds"
    save_rbds(rbd, str(path))
    assert '"advisor"' in path.read_text()
    with caplog.at_level(logging.WARNING):
        back = load_rbds(str(path))
    assert ignore.entries(back) == ignore.entries(rbd)
    assert "advisor" not in caplog.text


def test_a_model_with_nothing_ignored_writes_no_advisor_key(tmp_path):
    path = tmp_path / "m.rbds"
    save_rbds(flawed(), str(path))
    assert '"advisor"' not in path.read_text()

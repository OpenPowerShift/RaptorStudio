"""Importance measures: the variant edit, the algebra, and the report text.

The numbers here are checked against a model whose answer can be reasoned
out by hand, not against a recorded Raptor capture -- Raptor 7 has no
importance analysis to capture, so this is the one place in the test suite
that pins OUR arithmetic rather than parity.
"""
from __future__ import annotations


import pytest

from raptor.api import importance as imp
from raptor.api.build import build_from_spec
from raptor.core.engine.runner import run_simulation


def series_spec(blocks=("A", "B"), runs=400, length=1000.0, fail_mean=100.0):
    nodes = [{"id": 1, "name": "Start", "type": 1, "n": 0, "out": 1}]
    for i, _ in enumerate(blocks):
        nodes.append({"id": i + 2, "name": f"n{i}", "type": 2, "n": 1,
                      "out": 1})
    nodes.append({"id": len(blocks) + 2, "name": "End", "type": 3, "n": 1,
                  "out": 0})
    spec_blocks = []
    for i, name in enumerate(blocks):
        spec_blocks.append({
            "id": i + 1, "name": name,
            "fail_dist": 4, "fail_params": [fail_mean],
            "repair_dist": 7, "repair_params": [10.0, 2.0]})
    links = [{"from": "Start", "to": blocks[0]}]
    for i in range(len(blocks) - 1):
        links.append({"from": blocks[i], "to": blocks[i + 1]})
    links.append({"from": blocks[-1], "to": "End"})
    return {"name": "ImpTest", "nodes": nodes, "blocks": spec_blocks,
            "links": links,
            "system": {"num_runs": runs, "run_length": length}}


def _rbd(**kwargs):
    return build_from_spec(series_spec(**kwargs), auto_layout=False)


def _baseline(rbd, runs=400, seed=3):
    sink: list = []
    run_simulation(rbd, num_runs=runs, seed=seed, collect_logs=False,
                   _report_sink=sink)
    return sink


def test_batch_count_and_estimate():
    assert imp.batch_count(0, "both") == 1
    assert imp.batch_count(5, "improvement") == 6
    assert imp.batch_count(5, "risk") == 6
    assert imp.batch_count(5, "both") == 11
    assert imp.estimate_runs(5, "both", 100) == 1100


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        imp.variants_for("sideways")


def test_variant_touches_only_the_target_block():
    rbd = _rbd()
    before = {str(r["ID"]): dict(r) for r in rbd.section("BLOCK_DATA").rows}
    perfect = imp.variant(rbd, 1, "perfect", run_length=1000.0)
    after = {str(r["ID"]): dict(r) for r in perfect.section("BLOCK_DATA").rows}
    assert after["1"]["FDist"] == "19"
    assert float(after["1"]["FParam1"]) == 1001.0
    for bid in before:
        if bid != "1":
            assert after[bid] == before[bid]
    # The original model is untouched: a variant is a copy.
    assert {str(r["ID"]): dict(r) for r in
            rbd.section("BLOCK_DATA").rows} == before


def test_failed_variant_makes_the_block_unrepairable():
    rbd = _rbd()
    failed = imp.variant(rbd, 2, "failed", run_length=1000.0)
    row = next(r for r in failed.section("BLOCK_DATA").rows
               if str(r["ID"]) == "2")
    assert row["FDist"] == "19"
    assert float(row["FParam1"]) == 0.0
    assert row["RDist"] == "18"
    assert row["#RParam"] == "0"


def test_perfect_and_failed_bracket_the_baseline():
    rbd = _rbd(blocks=("A", "B", "C"), runs=200)
    base = _baseline(rbd, runs=200)
    result = imp.analyse(rbd, base, mode="both", runs=200, seed=3)
    a0 = result["metrics"]["Availability"]["baseline"]
    rows = {r["block"]: r for r in result["metrics"]["Availability"]["rows"]}
    assert set(rows) == {"A", "B", "C"}
    for name, row in rows.items():
        assert row["perfect"] >= a0 - 1e-9, name
        assert row["failed"] <= a0 + 1e-9, name
        assert row["birnbaum"] == pytest.approx(
            row["perfect"] - row["failed"])
    assert result["batches"] == 1 + 2 * 3


def test_the_algebra_between_the_measures_holds():
    """RRW = 1 / (1 - VF) for any block: both are functions of the same two
    numbers, so a slip in either shows up here."""
    rbd = _rbd(blocks=("A", "B"), runs=300)
    base = _baseline(rbd, runs=300)
    result = imp.analyse(rbd, base, mode="improvement", runs=300, seed=3)
    a0 = result["metrics"]["Availability"]["baseline"]
    for row in result["metrics"]["Availability"]["rows"]:
        vf = (row["perfect"] - a0) / (1.0 - a0)
        assert row["vesely_fussell"] == pytest.approx(vf, abs=1e-9)
        assert row["rrw"] == pytest.approx(1.0 / (1.0 - vf), abs=1e-6)


def test_a_system_that_never_fails_reports_no_risk_measures():
    """u0 = 0 has no risk to reduce or achieve: every ratio is undefined,
    and None (an em dash) is the only honest answer."""
    rbd = _rbd(blocks=("A",), runs=20, length=10.0, fail_mean=1.0e9)
    base = _baseline(rbd, runs=20)
    result = imp.analyse(rbd, base, mode="both", runs=20, seed=3)
    row = result["metrics"]["Availability"]["rows"][0]
    assert result["metrics"]["Availability"]["baseline"] == pytest.approx(1.0)
    assert row["raw"] is None and row["rrw"] is None
    assert row["vesely_fussell"] is None
    assert any("never failed" in note for note in result["notes"])


def test_report_text_uses_an_em_dash_for_undefined():
    rbd = _rbd(blocks=("A",), runs=20, length=10.0, fail_mean=1.0e9)
    base = _baseline(rbd, runs=20)
    result = imp.analyse(rbd, base, mode="improvement", runs=20, seed=3)
    text = "\n".join(imp.table_lines(result, "Availability"))
    assert "***IMPORTANCE_AVAILABILITY***" in text
    assert "—" in text


def test_metric_selector_covers_the_three_metrics():
    assert [label for _attr, label in imp.METRICS] == [
        "Availability", "Reliability", "Dependability"]

"""MCP fit_from_experience: shares and ages to a law, and the edits for a block."""
from __future__ import annotations

import pytest

from raptor.mcp.tools_optimise import fit_from_experience


def test_fit_returns_the_law_and_the_block_edits():
    result = fit_from_experience(
        "weibull", [{"probability": 0.1, "age": 100}, {"probability": 0.5, "age": 300}],
        block="Pump", use="fail")
    assert not result.is_error
    data = result.structured_content
    assert data["law"] == "weibull" and data["params"]["shape"] > 1
    assert data["changes"][0] == "block:Pump.fail_dist=weibull"
    assert data["update"]["fail_dist"] == "weibull"
    assert data["update"]["fail_params"] == pytest.approx(data["slots"])
    assert [row["probability"] for row in data["fitted"]] == [0.01, 0.1, 0.5, 0.9, 0.99]
    assert "Changes to apply" in result.content[0].text


def test_inconsistent_or_malformed_answers_are_refused():
    bad = fit_from_experience(
        "weibull", [{"probability": 0.1, "age": 300}, {"probability": 0.5, "age": 100}])
    assert bad.is_error and "inconsistent" in bad.content[0].text
    malformed = fit_from_experience("lognormal", [{"p": 0.1}, {"age": 3}])
    assert malformed.is_error and "probability" in malformed.content[0].text

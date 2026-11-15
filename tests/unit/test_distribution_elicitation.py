"""Fitting a life law from judged quantiles (WP-4.4)."""
from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from raptor.api.distributions import from_quantiles


def test_weibull_closed_form_round_trips():
    shape, scale = 2.0, 100.0
    q = {0.1: scale * (-math.log(0.9)) ** (1 / shape),
         0.5: scale * math.log(2) ** (1 / shape)}
    fit = from_quantiles("weibull", q)
    assert fit["params"]["shape"] == pytest.approx(shape, rel=1e-9)
    assert fit["params"]["scale"] == pytest.approx(scale, rel=1e-9)
    assert fit["mean"] == pytest.approx(scale * math.gamma(1.5), rel=1e-9)
    assert "wears out" in fit["shape_note"]


def test_lognormal_closed_form_round_trips():
    mu, sigma = math.log(100.0), 0.3
    q = {0.1: math.exp(mu + sigma * NormalDist().inv_cdf(0.1)),
         0.5: math.exp(mu)}
    fit = from_quantiles("lognormal", q)
    assert fit["params"]["mu"] == pytest.approx(mu, rel=1e-9)
    assert fit["params"]["sigma"] == pytest.approx(sigma, rel=1e-9)


def test_exponential_uses_the_median_and_says_pm_cannot_help():
    q = {0.1: -100 * math.log(0.9), 0.5: -100 * math.log(0.5)}
    fit = from_quantiles("exponential", q)
    assert fit["params"]["mean"] == pytest.approx(100.0, rel=1e-9)
    assert "cannot" in fit["shape_note"]


def test_three_quantiles_fit_least_squares():
    shape, scale = 1.7, 250.0
    q = {p: scale * (-math.log(1 - p)) ** (1 / shape)
         for p in (0.1, 0.5, 0.9)}
    fit = from_quantiles("weibull", q)
    assert fit["params"]["shape"] == pytest.approx(shape, rel=1e-6)
    assert fit["params"]["scale"] == pytest.approx(scale, rel=1e-6)


def test_inconsistent_judgement_is_refused_by_name():
    with pytest.raises(ValueError, match="higher probability"):
        from_quantiles("weibull", {0.5: 100, 0.1: 300})
    with pytest.raises(ValueError, match="at least two"):
        from_quantiles("weibull", {0.5: 100})
    with pytest.raises(ValueError, match="positive"):
        from_quantiles("weibull", {0.1: -1, 0.5: 100})
    with pytest.raises(ValueError, match="law"):
        from_quantiles("beta", {0.1: 1, 0.5: 2})


def _model():
    from raptor.api import build
    return build.build_from_spec({
        "blocks": [{"name": "Valve", "fail_dist": "Exponential", "fail_params": [400],
                    "repair_dist": "Exponential", "repair_params": [2]}],
        "nodes": [{"name": "Start", "type": 1}, {"name": "End", "type": 3}],
        "links": [{"from": "Start", "to": "Valve"}, {"from": "Valve", "to": "End"}]})


@pytest.mark.parametrize(("law", "use", "code"), [("weibull", "fail", 15),
                                                   ("lognormal", "repair", 7),
                                                   ("exponential", "fail", 4)])
def test_a_fit_applies_to_a_block_as_one_edit(law, use, code):
    # the law and its parameters go in together: one at a time, a Weibull
    # was checked against the exponential's parameters (scale 0) and refused
    from raptor.api.distributions import judgement_changes
    from raptor.api.paths import cell_changes
    fit = from_quantiles(law, {0.1: 100, 0.5: 300})
    changes = judgement_changes(fit, "Valve", use)
    cells = {c["column"]: c["new"] for c in cell_changes(_model(), changes)}
    prefix = "F" if use == "fail" else "R"
    if code != 4:
        assert int(float(cells[f"{prefix}Dist"])) == code
    assert float(cells[f"{prefix}Param1"]) == pytest.approx(fit["slots"][0], rel=1e-6)


def test_set_takes_a_new_law_and_its_parameters_together():
    from raptor.api.paths import cell_changes
    cells = {c["column"]: c["new"] for c in cell_changes(
        _model(), ["block:Valve.fail_dist=weibull", "block:Valve.fail_params=1.5,500"])}
    assert int(float(cells["FDist"])) == 15
    assert float(cells["FParam2"]) == pytest.approx(500.0)


def test_cli_fit_prints_the_changes_for_a_block(capsys):
    import json
    from raptor.cli.main import main
    assert main(["fit", "--law", "weibull", "--quantile", "0.1=100",
                 "--quantile", "0.5=300", "--block", "Pump A", "-f", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["changes"][0] == "block:Pump A.fail_dist=weibull"
    assert main(["fit", "--law", "lognormal", "--quantile", "0.5=4",
                 "--quantile", "0.9=12", "--block", "Pump A", "--repair"]) == 0
    assert "raptor set MODEL --in-place 'block:Pump A.repair_dist=lognormal'" in capsys.readouterr().out

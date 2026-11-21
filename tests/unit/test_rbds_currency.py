"""A model's currency: saved in .rbds, printed in front of money everywhere."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor.api.build import build_from_spec, series
from raptor.api.insight import charts, cost_series, story
from raptor.api.insight.axes import currency, format_value, tick_text, with_currency
from raptor.core.fileio.rbds import clean_currency, load_rbds, render_rbds
from raptor.render import charts_svg

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "ReleaseProbe27" / "legacy"


def _cost_runs():
    return cost_series.load_runs(
        str(GOLDEN / "rpt_interim_cost_ReleaseProbe27.txt"),
        str(GOLDEN / "rpt_end_of_run_ReleaseProbe27.txt"))


def test_currency_round_trips_and_the_default_is_not_written(tmp_path):
    rbd = build_from_spec(series(2))
    assert rbd.currency == "$"
    assert "currency" not in json.loads(render_rbds(rbd))
    rbd.currency = "€"
    text = render_rbds(rbd)
    assert json.loads(text)["currency"] == "€"
    path = tmp_path / "euro.rbds"
    path.write_text(text, encoding="utf-8")
    assert load_rbds(str(path)).currency == "€"


@pytest.mark.parametrize(("raw", "clean"), [
    (None, "$"), ("", "$"), ("   ", "$"), (12, "$"), (" AUD ", "AUD"),
    ("A\x00$", "A$"), ("TOOLONGCODE", "TOOLONGC"),
])
def test_unusable_currency_text_falls_back_or_is_trimmed(raw, clean):
    assert clean_currency(raw) == clean


def test_currency_is_not_part_of_the_model_identity():
    from raptor.api.run import model_hash
    rbd = build_from_spec(series(2))
    before = model_hash(rbd)
    rbd.currency = "£"
    assert model_hash(rbd) == before


def test_money_is_printed_in_the_current_currency():
    assert format_value(1.8e6, "money") == "$1.80M"
    with currency("€"):
        assert format_value(1.8e6, "money") == "€1.80M"
        assert format_value(-12_000, "money") == "-€12.0k"
        assert tick_text(2e6, [0, 1e6, 2e6], fmt="money") == "€2M"
    assert with_currency("1,234", "AUD") == "AUD 1,234"   # a code gets a space
    assert format_value(1.8e6, "money") == "$1.80M"        # restored after


def test_a_cost_chart_draws_and_reads_in_the_model_currency():
    spec = charts.chart_named("cost-categories", cost_runs=_cost_runs(), currency="€")
    assert spec.provenance["currency"] == "€"
    # drawn later, outside the block, it still uses the chart's own currency
    svg = charts_svg.render(spec)
    assert "€" in svg and "$" not in svg
    lines = charts.text_lines(spec)
    assert any("€" in line for line in lines[1:])
    folded = charts.fold_small(spec, top=1)
    assert folded.provenance["currency"] == "€"
    default = charts.chart_named("cost-categories", cost_runs=_cost_runs())
    assert default.provenance["currency"] == "$"


def test_the_story_prints_the_cost_in_the_model_currency():
    per_run = [{"availability": 0.99, "grand_total": 2.5e8},
               {"availability": 0.98, "grand_total": 2.7e8}]
    built = story.build(per_run, cost_on=True, currency="€")
    chip = next(c for c in built.numbers if c.field == "grand_total")
    assert story.chip_text(chip) == "€260,000,000"
    section = next(s for s in built.sections if s.key == "cost")
    # grand_total is per run: the Story must not call the mean a batch total
    assert section.line == "A run costs €260,000,000 on average."
    assert "€260,000,000" in "\n".join(story.text_lines(built))

"""Axis ticks and value labels shared by the Qt and SVG chart renderers."""
from raptor.api.insight.axes import format_value, nice_range, nice_ticks, tick_text


def test_ticks_are_round_and_cover_the_range():
    assert nice_ticks(0.0, 1.0, 5) == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    low, high = nice_range(0.9935, 0.9991, 4)
    assert low <= 0.9935 and high >= 0.9991
    assert (low, high) == (0.992, 1.0)      # step 0.002, by hand


def test_neighbouring_percent_ticks_never_print_alike():
    ticks = nice_ticks(0.9990, 1.0, 5)
    labels = [tick_text(t, ticks, fmt="percent") for t in ticks]
    assert len(set(labels)) == len(labels)
    assert labels == ["99.900 %", "99.925 %", "99.950 %", "99.975 %",
                      "100.000 %"]


def test_money_is_short():
    assert format_value(1_800_000, "money") == "1.80M"
    assert format_value(4_690_000, "money") == "4.69M"
    assert format_value(950, "money") == "950"
    assert format_value(0.9934, "percent") == "99.34 %"

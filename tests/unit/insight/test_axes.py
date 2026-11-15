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


def test_pct_never_prints_scientific_notation():
    from raptor.api.insight.axes import pct
    assert pct(0.4381) == "43.8 %"
    assert pct(4.73e-11) == "< 0.01 %"
    assert pct(0.0) == "0 %"
    assert "e" not in pct(2.5e-6)


def test_placed_labels_never_overlap_and_crowded_ones_get_a_leader():
    from raptor.api.insight.axes import _overlaps, place_labels
    # five points almost on top of each other, as High_Lift_Pump_1..3 were
    points = [(100.0, 200.0 + i, 5.0) for i in range(5)]
    placed = place_labels(points, [(90.0, 14.0)] * 5, (0.0, 0.0, 600.0, 400.0))
    rects = [rect for rect, _end in placed]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not _overlaps(a, b)
    assert any(end is not None for _rect, end in placed)
    assert placed[0][1] is None          # the first keeps its place by the dot

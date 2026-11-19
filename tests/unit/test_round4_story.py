"""Fourth round of new-user testing: the Story tells the truth about rare,
absent and cut-short results, names what keeps the system down, and takes a
target from the command line, MCP and the Charts page."""
from __future__ import annotations

import os
import tempfile

import pytest

from raptor.api.insight import drivers
from raptor.api.insight.dots import meets, split_at
from raptor.api.insight.story import build, text_lines

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _runs(n=20, **fields):
    base = {"availability": 0.99, "reliability": 0.0, "mtbde": 700.0, "mdt": 8.0,
            "total_time": 8760.0, "ending_sim_time": 8760.0, "red_percent": 1.2,
            "num_system_failures": 12}
    base.update(fields)
    return [dict(base) for _ in range(n)]


def _chip(story, field):
    return next(c for c in story.numbers if c.field == field)


def test_time_chips_are_pooled_and_match_the_down_line():
    rows = _runs(2) + _runs(2, num_system_failures=0, red_percent=0.0)
    story = build(rows, units="hours")
    outages = 24
    down = 2 * 8760 * 0.012
    assert _chip(story, "mdt").value == pytest.approx(down / outages)
    up = 2 * 8760 * (1 - 0.012) + 2 * 8760
    assert _chip(story, "mtbde").value == pytest.approx(up / outages)


def test_no_outage_is_not_a_measured_time_between_failures():
    story = build(_runs(20, num_system_failures=0, red_percent=0.0,
                        ending_sim_time=100.0, total_time=100.0), units="hours")
    text = "\n".join(text_lines(story))
    assert "none in 20 runs (more than 100 hours)" in text
    assert "Time down per outage" not in text


def test_few_outages_are_called_rough_and_large_times_are_plain():
    rows = _runs(30, num_system_failures=0, red_percent=0.0)
    rows[0]["num_system_failures"] = 2
    chip = _chip(build(rows, units="hours"), "mtbde")
    assert "e+" not in chip.text and "from 2 outages" in chip.text


def test_rounding_is_not_others():
    attribution = {"runs": 2, "units": "hours", "causes": {}, "elements": [
        {"name": n, "kind": "block", "id": i, "dtci": {"mean": v, "sem": 0.0},
         "red_time": {"mean": v}, "causes": {"repair": 1.0}}
        for i, (n, v) in enumerate((("A", 0.821), ("B", 0.179 + 1.1e-14)), 1)],
        "system": {"red_time": {"mean": 1.0}, "outages": {"mean": 1.0}}}
    story = build(_runs(), attribution=attribution)
    assert story.other_drivers_share == 0.0
    assert "others" not in "\n".join(text_lines(story))


@pytest.mark.parametrize("op, met", [(">=", 2), ("<=", 2), ("<", 1), (">", 1)])
def test_a_target_is_judged_by_its_own_comparison(op, met):
    assert split_at([4.0, 5.0, 9.0], 5.0, op) == (met, 3)
    assert meets(4.0, 5.0, "<=") and not meets(4.0, 5.0, ">=")
    story = build([{"mdt": v, "availability": 0.9} for v in (4.0, 9.0, 9.0)],
                  units="hours", requirements=[{"metric": "mdt", "op": "<=", "value": 5}])
    assert story.met == 1 and story.proportion == "1 of 3 runs met the target"


def test_a_reliability_gap_is_not_turned_into_hours():
    story = build(_runs(), units="hours",
                  requirements=[{"metric": "reliability", "op": ">=", "value": 0.5}])
    assert "h a year" not in story.headline.gap_text
    story = build(_runs(), units="hours",
                  requirements=[{"metric": "availability", "op": ">=", "value": 0.9999}])
    assert "e+" not in story.headline.gap_text and "h a year" in story.headline.gap_text


def test_a_cut_short_or_single_run_says_so_first():
    story = build(_runs(502), requested_runs=100_000, truncated="time limit")
    assert story.findings[0].title == "Stopped early"
    assert "502 of the 100,000" in story.findings[0].message
    assert "502 runs of 100,000 asked for" in "\n".join(text_lines(story))
    one = build(_runs(1))
    assert one.findings[0].title == "One run"
    assert "1 run ·" in "\n".join(text_lines(one))


def test_a_model_with_no_run_length_leads_with_reliability():
    rows = [{"availability": 1.0, "reliability": r, "num_system_failures": f}
            for r, f in ((1.0, 0),) * 29 + ((0.0, 1),)]
    story = build(rows)
    assert story.headline.field == "reliability"
    assert "availability" not in [c.field for c in story.numbers]


def test_non_mission_phase_downtime_is_named_not_counted_as_an_element():
    attribution = {"runs": 3, "units": "minutes", "causes": {"other": 0.93},
                   "phases": [{"name": "Soap", "mission": False, "share_of_downtime": 0.95}],
                   "system": {"red_time": {"mean": 145.0}, "outages": {"mean": 69.0}},
                   "elements": [
                       {"name": "(no single element)", "kind": "system", "id": 0,
                        "dtci": {"mean": 0.93, "sem": 0.01}, "red_time": {"mean": 135.0},
                        "causes": {"other": 1.0}, "main_cause": "other"},
                       {"name": "Blower", "kind": "block", "id": 1,
                        "dtci": {"mean": 0.07, "sem": 0.01}, "red_time": {"mean": 10.0},
                        "causes": {"repair": 0.6}, "main_cause": "repair"}]}
    text = drivers.caption(attribution)
    assert text.startswith("93 % of system downtime is time in a non-mission phase (Soap)")
    assert "1 element causes" in text and "(no single element)'s" not in text
    assert build(_runs(), attribution=attribution).drivers[0].name == "(non-mission phase)"


def test_run_count_advice_does_not_contradict_itself():
    from raptor.api.advisor.finding import Finding, sort_findings

    def finding(rule):
        return Finding(rule=rule, severity="advice", category="x", title=rule,
                       message=rule, why="")
    kept = sort_findings([finding("M-EXP-002"), finding("R-QUA-001"), finding("M-EXP-001")])
    assert {f.rule for f in kept} == {"R-QUA-001"}
    assert {f.rule for f in sort_findings([finding("M-EXP-002")])} == {"M-EXP-002"}


def test_an_availability_near_one_keeps_its_shortfall():
    from raptor.api.advisor.text import fmt_avail
    assert fmt_avail(0.999932) == "99.9932 %" and fmt_avail(1.0) == "100 %"


def _workspace(spec):
    from raptor.api.build import build_from_spec
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    mid = ws.register_model(build_from_spec(spec), name="m")["model_id"]
    return ws, mid


def test_a_backup_held_down_by_its_unit_is_the_advice():
    """A and B in parallel, B follows A: "Shorter repairs help most" missed
    that B gives no redundancy."""
    from raptor.api import run as run_api
    from raptor.api.advisor import advise_stored_run
    from raptor.api.build import update_element
    from raptor.api.build import parallel
    spec = parallel(2)
    for block in spec["blocks"]:
        block.update(fail_dist=4, fail_params=[500.0], repair_dist=4, repair_params=[20.0])
    ws, mid = _workspace(spec)
    update_element(ws, mid, "Block2", {"dep_type": "RBDBlock", "dep_num": 1, "dep_idle": 100})
    result = run_api.run(ws, mid, num_runs=10, attribution=True, confirm_long_run=True)
    found = [f.message for f in advise_stored_run(ws, result.run_id) if f.rule == "R-DRV-002"]
    assert found and "takes away the redundancy" in found[0]


def test_a_stopped_batch_keeps_its_runs_when_attribution_was_asked_for():
    from raptor.api import run as run_api
    from raptor.api.build import series
    ws, mid = _workspace(series(3))
    result = run_api.run(ws, mid, num_runs=200_000, attribution=True, timeout_s=1.0,
                         confirm_long_run=True)
    assert 0 < len(result.per_run) < 200_000
    assert any("attribution" in w.lower() for w in result.warnings)
    with pytest.raises(drivers.NoAttribution, match="stopped"):
        drivers.read(ws, result.run_id)


def test_explain_takes_a_time_limit_and_a_target():
    from raptor.cli.main import build_parser
    args = build_parser().parse_args(["explain", "m.rbds", "--timeout", "90s",
                                      "--target", "mdt<=5"])
    assert args.timeout == "90s" and args.target == ["mdt<=5"]


# --- the target line in the window ----------------------------------------------

@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_charts_target_draws_a_line_and_judges_the_story(qapp, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from raptor.gui.ui.results import ResultsDock
    dock = ResultsDock()
    page = dock._charts_page
    page.set_inputs(per_run=[{"availability": a} for a in (0.9, 0.95, 0.99)])
    page.select("trial-outcome")
    assert page.target_button.isVisibleTo(page)
    assert "visual cue" in page.target_button.toolTip()
    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (95.0, True)))
    seen = []
    page.target_changed.connect(seen.append)
    page.target_button.click()
    assert seen == [0.95] and dock.target == 0.95
    assert page.target_button.text() == "Target: 95 %"
    assert {"kind": "target", "value": 0.95} in page.selected_spec().marks
    monkeypatch.setattr(QInputDialog, "getDouble",
                        staticmethod(lambda *a, **k: (0.0, True)))
    page.target_button.click()                     # 0 clears it
    assert dock.target is None and page.target_button.text() == "Target: none"


def test_the_story_page_says_where_the_target_came_from(qapp):
    from raptor.gui.ui.story_page import StoryPage
    page = StoryPage()
    page.set_story(build(_runs(5), requirements=[
        {"metric": "availability", "op": ">=", "value": 0.95}]))
    page.set_target_note("target set on the Charts page")
    assert page.headline.isVisibleTo(page)
    assert "Charts page" in page.target.text()

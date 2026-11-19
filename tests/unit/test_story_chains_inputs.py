"""Outage chains without zero-length "(other)" steps, read as hand-overs
when two elements take turns; and a very low result traced to its inputs."""
from __future__ import annotations

import tempfile

import pytest

from raptor.api.insight.drivers import chain_text


def _step(element, cause="repair"):
    return {"element": element, "cause": cause}


def test_two_elements_taking_turns_read_as_hand_overs():
    seq = {"steps": [_step("Junk"), _step("Junk2"), _step("Junk"), _step("Junk2")]}
    assert chain_text(seq) == ("Junk (hands-on repair) and Junk2 (hands-on repair) "
                               "in turn, 3 hand-overs")
    seq["more_steps"] = 4.6
    assert "about 8 hand-overs" in chain_text(seq)


def test_a_long_chain_says_it_goes_on():
    seq = {"steps": [_step("A"), _step("B", "wait_spare"), _step("C")], "more_steps": 1}
    assert chain_text(seq).endswith("-> … (1 more step on average)")
    assert chain_text({"steps": []}) == "(unknown)"


def _workspace(spec):
    from raptor.api.build import build_from_spec
    from raptor.api.workspace import Workspace
    ws = Workspace(tempfile.mkdtemp())
    return ws, ws.register_model(build_from_spec(spec), name="m")["model_id"]


def _two_in_series(life=(10.0, 20.0), repair=(40.0, 50.0)):
    from raptor.api.build import series
    spec = series(2)
    for block, f, r in zip(spec["blocks"], life, repair):
        block.update(fail_dist=4, fail_params=[f], repair_dist=4, repair_params=[r])
    return spec


@pytest.fixture(scope="module")
def low_run():
    from raptor.api import run as run_api
    ws, mid = _workspace(_two_in_series())
    result = run_api.run(ws, mid, num_runs=10, attribution=True, confirm_long_run=True)
    return ws, result.run_id


def test_no_instant_failed_step_in_the_chains(low_run):
    from raptor.api.insight import drivers
    ws, run_id = low_run
    attribution = drivers.read(ws, run_id)
    causes = {s["cause"] for seq in attribution["sequences"] for s in seq["steps"]}
    assert "other" not in causes
    assert "(other)" not in "\n".join(drivers.text_lines(attribution))
    assert any(seq.get("more_steps") for seq in attribution["sequences"])


def test_a_very_low_result_points_at_the_inputs(low_run):
    from raptor.api.advisor import advise_stored_run
    from raptor.api.insight.story import from_run
    ws, run_id = low_run
    found = [f for f in advise_stored_run(ws, run_id) if f.rule == "R-DAT-001"]
    assert found and found[0].severity == "warning"
    message = found[0].message
    assert message.startswith("The system is down 9") and "Block1" in message
    assert "Block2" in message and "swapped or in different units" in message
    assert from_run(ws, run_id).findings[0].rule == "R-DAT-001"


def test_a_low_result_with_sound_inputs_is_not_blamed_on_them():
    from raptor.api.advisor import advise_run
    from raptor.api.build import build_from_spec
    rbd = build_from_spec(_two_in_series(life=(100.0, 100.0), repair=(10.0, 10.0)))
    per_run = [{"availability": 0.3}] * 5
    assert not [f for f in advise_run(rbd, {}, per_run) if f.rule == "R-DAT-001"]


def test_redundancy_is_read_from_the_drawing():
    from raptor.api.advisor.context import ModelContext
    from raptor.api.build import build_from_spec, parallel, series
    ctx = ModelContext(build_from_spec(parallel(2)))
    assert all(ctx.redundant(("block", int(b["id"]))) for b in ctx.blocks)
    ctx = ModelContext(build_from_spec(series(2)))
    assert not any(ctx.redundant(("block", int(b["id"]))) for b in ctx.blocks)

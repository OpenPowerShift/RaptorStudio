"""The Avant-garde defer trigger against real captures.

`DeferTrigProbe` (two parallel blocks, one commented `maintDefer`, a trigger `DeferTrig`, the system
comment `The defer trigger is DeferTrig`) was run in real Raptor twice: without the master switch
(the Simulation dialog's OK clears `deferTrig`, so nothing defers) and with it (the combo keeps the
file's trigger)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.log_compare import read_capture_text

from raptor.core.engine.eventlog import format_event_log_file
from raptor.core.engine.load import load_model, resolve_defer_trigger, saved_defer_trigger
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden"


def _real(name: str, stem: str) -> list[str]:
    raw = read_capture_text(GOLD / name / "legacy" / stem)
    return raw.replace("\r", "").split("\n")


def _ours(name: str, choice: str) -> list[str]:
    rbd = load_rbd(str(GOLD / name / "input.rbd"))
    logs = run_simulation(rbd, defer_trigger=choice)
    return [str(x) for x in format_event_log_file(logs, "input.rbd", "DATE")]


@pytest.mark.parametrize("choice,stem", [("", "rpt_events_DeferTrigProbe.txt"),
                                         ("file", "rpt_events_password_DeferTrigProbe.txt")])
def test_event_log_matches_real(choice, stem):
    real = _real("DeferTrigProbe", stem)
    ours = _ours("DeferTrigProbe", choice)
    assert ours[1:] == real[1:len(ours)]
    assert real[len(ours):] in ([], [""])


def test_switch_off_means_no_deferral():
    assert not any("Failed-deferrin" in ln for ln in _ours("DeferTrigProbe", ""))
    assert any("Failed-deferrin" in ln for ln in _ours("DeferTrigProbe", "file"))


def test_the_file_carries_the_trigger_and_the_block_comment():
    rbd = load_rbd(str(GOLD / "DeferTrigProbe" / "input.rbd"))
    assert saved_defer_trigger(rbd) == "DeferTrig"
    model = load_model(rbd)
    assert model.saved_defer_trigger == "DeferTrig" and model.defer_trigger == ""
    assert [b.comment for b in model.blocks_by_id.values()].count("maintDefer") == 1


def test_choice_resolution():
    rbd = load_rbd(str(GOLD / "DeferTrigProbe" / "input.rbd"))
    assert resolve_defer_trigger(rbd, "") == ""
    assert resolve_defer_trigger(rbd, "file") == "DeferTrig"
    assert resolve_defer_trigger(rbd, "File") == "DeferTrig"
    assert resolve_defer_trigger(rbd, "Other") == "Other"
    plain = load_rbd(str(GOLD / "Phasing3" / "input.rbd"))
    assert saved_defer_trigger(plain) == ""
    assert resolve_defer_trigger(plain, "file") == "500W-PowerString"   # the combo's first entry
    flat = load_rbd(str(GOLD / "MotorDemo" / "input.rbd"))
    assert resolve_defer_trigger(flat, "file") == ""


def test_prefix_rule_is_the_first_21_characters():
    from raptor.core.engine.load import _DEFER_PREFIX
    assert len(_DEFER_PREFIX) == 21


def test_a_trigger_that_does_not_exist_holds_nothing():
    """`FOREACH trig IN triggerGroup` finds no trigger of that name: the block is in RepHold
    but nothing waits (it goes straight on to its repair). Source-derived, not captured."""
    ours = _ours("DeferTrigProbe", "NoSuchTrigger")
    held = [i for i, ln in enumerate(ours) if "Failed-deferrin" in ln]
    assert held
    assert "Failed" in ours[held[0] + 3] or any("Failed " in ln for ln in ours[held[0]:held[0] + 6])


def test_phase_trigger_probe_without_the_switch_matches_real():
    """`DeferPhaseTrigProbe` (a clone of Phasing3: five blocks commented `maintDefer`, a trigger
    named `phaseTrigger`, the system comment naming it): with the switch off nothing defers and
    all 52,977 lines are exact."""
    real = _real("DeferPhaseTrigProbe", "rpt_events_DeferPhaseTrigProbe.txt")
    ours = _ours("DeferPhaseTrigProbe", "")
    assert ours[1:] == real[1:len(ours)]


def test_phase_trigger_probe_with_the_switch_matches_real():
    """With the switch on (71 deferrals, the held blocks freed by `phaseTrigger`'s release at each
    mission end) all 52,757 lines are exact. The one instant that used to differ (t=1952.609639, last run):
    a `maintDefer` block entering RepHold ("deferred maintenance") is not a `CheckColds` site, so the cold
    survey it marked waits for BatteriesNiH's `Repair` start (gdb trace
    an internal trace)."""
    real = _real("DeferPhaseTrigProbe", "rpt_events_password_DeferPhaseTrigProbe.txt")
    ours = _ours("DeferPhaseTrigProbe", "file")
    assert ours[1:] == real[1:len(ours)]
    assert real[len(ours):] in ([], [""])

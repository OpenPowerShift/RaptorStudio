"""Branch tests for engine modules that had thin or no coverage floors.

`conditional_life` (age features), `report_rows` (row element tags),
`extended_results` and the auto-stopped MMT branches of
`build_run_report_data`.

Every numeric expectation is a hand calculation, written out in the test's
docstring so a reviewer can redo it with a calculator and the formulas in
the manual (survival functions: docs/engine-design; Raptor 7 n/(n-1)
censoring: the report-format notes). The RNG is a stub that returns fixed
values, so the path taken -- and therefore the arithmetic -- is exact. These
tests pin behaviour; they never relax a floor.
"""
from __future__ import annotations

import math
import pickle

import pytest

from raptor.core.engine.conditional_life import (
    _INSTANT,
    _bisect,
    _closed_form,
    conditional_life,
)
from raptor.core.engine.report_rows import (
    ElementLine,
    apply_tags,
    element_of,
    tag,
    tags_of,
)
from raptor.core.rng import Distribution as D


class _FakeRng:
    """Returns `life` for every life draw and `u` for the Uniform(0,1) draw
    used by inverse sampling, so the path taken is fully determined."""

    def __init__(self, life: float, u: float = 0.5) -> None:
        self.life, self.u = life, u

    def draw_number(self, dist, params):
        return self.u if dist == D.UNIFORM_REAL else self.life


# ---------------------------------------------------------------- conditional_life

def test_rejection_accepts_a_draw_beyond_the_age() -> None:
    """Normal(10, 2) block aged 5. The first draw (50) exceeds the age, so it
    is returned unchanged: plain rejection is exact and costs one draw."""
    assert conditional_life(_FakeRng(50.0), int(D.NORMAL), [10.0, 2.0], 5.0) == 50.0


def test_law_without_survival_function_keeps_rejecting_then_fails_instantly() -> None:
    """Poisson has no survival function, so inverse sampling is unavailable.
    Every draw (1) is below the age (5), so after the 10 cheap tries and the
    1000-try fallback the block fails straight away: life = age + 1e-6."""
    # Poisson has no survival function: the draws never exceed the age.
    life = conditional_life(_FakeRng(1.0), int(D.POISSON), [3.0, 0.0, 0.0], 5.0)
    assert life == 5.0 + _INSTANT


def test_law_without_survival_function_accepts_a_late_rejection_draw() -> None:
    """Same law, but the 12th draw (99) exceeds the age 5. Draws 1-10 are the
    cheap tries, draw 11 the first fallback try; draw 12 is returned as 99."""
    class Late(_FakeRng):
        calls = 0

        def draw_number(self, dist, params):
            Late.calls += 1
            # Fail the 10 cheap tries, succeed in the long fallback loop.
            return 99.0 if Late.calls > 11 else 1.0

    assert conditional_life(Late(0), int(D.POISSON), [3.0, 0.0, 0.0], 5.0) == 99.0


def test_age_past_a_bounded_support_fails_instantly() -> None:
    """Fixed(10) can never exceed 10. A block aged 12 has outlived the only
    life the law can produce, so it fails at once: 12 + 1e-6."""
    # Fixed(10) has support (10, 10); a block aged 12 has outlived it.
    assert conditional_life(_FakeRng(1.0), int(D.FIXED), [10.0], 12.0) == 12.0 + _INSTANT


def test_exponential_is_memoryless_via_closed_form() -> None:
    """Exponential(mean 100, location 0) aged 40, u = 1/e.

    Memoryless: time left = -mean * ln(u) = -100 * (-1) = 100, whatever the
    age, so the full life is age + 100 = 140."""
    life = conditional_life(_FakeRng(0.0, u=math.exp(-1.0)),
                            int(D.EXPONENTIAL), [100.0, 0.0], 40.0)
    assert life == pytest.approx(140.0)


def test_weibull_closed_form_exceeds_age() -> None:
    """Weibull(shape 2, scale 10, location 0) aged 8, u = 0.5.

    Conditional life solves sf(t)/sf(8) = u with sf(t) = exp(-(t/10)^2):
        t = 10 * ((8/10)^2 - ln 0.5)^(1/2)
          = 10 * (0.64 + 0.693147)^0.5 = 10 * 1.154620 = 11.5462."""
    life = conditional_life(_FakeRng(0.0, u=0.5), int(D.WEIBULL), [2.0, 10.0, 0.0], 8.0)
    assert life == pytest.approx(11.5462, abs=1e-4)


def test_closed_form_is_none_for_other_laws() -> None:
    """Only Exponential and Weibull have an exact inverse; Normal does not."""
    assert _closed_form(int(D.NORMAL), [10.0, 2.0], 5.0, 0.5) is None


def test_underflowed_survival_returns_instant_failure() -> None:
    """Normal(10, 1) aged 80 is 70 standard deviations out: sf underflows to
    exactly 0, so no honest life exists beyond it and the block fails at
    once (80 + 1e-6)."""
    life = conditional_life(_FakeRng(0.0), int(D.NORMAL), [10.0, 1.0], 80.0)
    assert life == 80.0 + _INSTANT


def test_inverse_sampling_by_bisection_beyond_the_rejection_tries() -> None:
    """Normal(10, 2) aged 14, u = 0.5; every ordinary draw (0) is rejected.

    z(age) = (14-10)/2 = 2, so sf(age) = 0.0227501 (standard normal table).
    Target survival = u * sf(age) = 0.0113751, i.e. z = 2.27761 from the
    table, so life = 10 + 2 * 2.27761 = 14.5552."""
    life = conditional_life(_FakeRng(0.0, u=0.5), int(D.NORMAL), [10.0, 2.0], 14.0)
    assert life == pytest.approx(14.5552, abs=1e-3)


def test_bisect_expands_its_bracket_when_the_first_upper_bound_is_too_low() -> None:
    """Normal(100, 5) from age 0 with target survival 0.25.

    sf = 0.25 is the upper quartile: z = 0.67449, so t = 100 + 5 * 0.67449
    = 103.3724. The first bracket [0, 1] is far below it, so the upper bound
    has to widen several times before bisection starts."""
    t = _bisect(int(D.NORMAL), [100.0, 5.0], 0.0, 0.25)
    assert t == pytest.approx(103.3724, abs=1e-3)


# ---------------------------------------------------------------- report_rows

def test_element_line_is_a_str_with_an_element() -> None:
    """A tagged row reads exactly like the legacy text (`==` the plain str)
    and also carries its (kind, id); an untagged string has no element."""
    line = tag("Armature   3", "block", 7)
    assert isinstance(line, ElementLine) and line == "Armature   3"
    assert element_of(line) == ("block", 7)
    assert element_of("plain") is None


def test_element_line_survives_pickling() -> None:
    """Rows cross process boundaries with a run's results (multiprocessing),
    so the tag must survive a pickle round trip."""
    line = pickle.loads(pickle.dumps(tag("x", "node", 2)))
    assert line == "x" and line.element == ("node", 2)


def test_tags_round_trip_through_apply_tags() -> None:
    """Lines 1 and 3 (0-based) are tagged, so the saved form is
    [[1, 'event', 4], [3, 'hier', 9]]; applying it to the bare text restores
    exactly those two tags."""
    lines = ["a", tag("b", "event", 4), "c", tag("d", "hier", 9)]
    saved = tags_of(lines)
    assert saved == [[1, "event", 4], [3, "hier", 9]]
    back = apply_tags([str(x) for x in lines], saved)
    assert [element_of(x) for x in back] == [None, ("event", 4), None, ("hier", 9)]


def test_apply_tags_ignores_bad_entries() -> None:
    """A hand-edited or older file may hold junk tags; each is skipped
    without disturbing the text."""
    lines = ["a", "b"]
    bad = [
        [5, "block", 1],     # index out of range
        [-1, "block", 1],    # negative index
        [0, "bogus", 1],     # unknown kind
        ["x", "block", 1],   # non-numeric index
        [0, "block"],        # too short
        None,                # not a sequence
    ]
    out = apply_tags(lines, bad)
    assert out == lines and all(element_of(x) is None for x in out)


# ---------------------------------------------------- auto-stopped MMT branches

def _state(repairs=(), pms=()):
    from raptor.core.engine.system_status import SystemState

    st = SystemState()
    st.green_stat.add(100.0)
    for v in repairs:
        st.comp_repair_stat.add(v)
    for v in pms:
        st.comp_pm_stat.add(v)
    return st


def _report(st):
    from raptor.core.engine.reporting import build_run_report_data

    return build_run_report_data(1, 100.0, 100.0, st, [],
                                 termination_type="FailSim", auto_stopped=True)


def test_auto_stopped_mmt_combines_repairs_and_pms() -> None:
    """FailSim run, last down period unfinished (Raptor 7 `autoStopped`).

    Repairs 2, 4 (n=2, mean 3):  MMTu = 3 * 2/(2-1) = 6
    PMs     1, 3 (n=2, mean 2):  MMTs = 2 * 2/(2-1) = 4
    MMT = (MMTu*(2-1) + MMTs*(2-1)) / ((2-1) + (2-1)) = (6 + 4)/2 = 5"""
    rd = _report(_state(repairs=(2.0, 4.0), pms=(1.0, 3.0)))
    assert rd.mmtu == pytest.approx(6.0)
    assert rd.mmts == pytest.approx(4.0)
    assert rd.mmt == pytest.approx(5.0)


def test_auto_stopped_mmt_with_pm_only() -> None:
    """No repairs, PMs 1 and 3 (n=2, mean 2): MMTs = 2 * 2/1 = 4, and with
    nothing else MMT = MMTs = 4 while MMTu is not defined."""
    rd = _report(_state(pms=(1.0, 3.0)))
    assert rd.mmts == pytest.approx(4.0)
    assert rd.mmt == rd.mmts
    assert rd.mmtu is None


def test_auto_stopped_mmt_with_too_little_data_is_none() -> None:
    """One repair (n=1) cannot be censored by n/(n-1) = 1/0, and there are no
    PMs, so all three means are undefined rather than a divide-by-zero."""
    rd = _report(_state(repairs=(2.0,)))
    assert rd.mmt is None and rd.mmtu is None and rd.mmts is None


# ------------------------------------------------------- extended_results

class _Run:
    """Just the attributes `build_extended_results_section` reads."""

    def __init__(self, criticality=None, censored=False, **measures) -> None:
        self.criticality = criticality
        self.mttff_censored = censored
        self.__dict__.update(measures)


def test_extended_section_non_finite_and_single_run_cells_are_na() -> None:
    """inf/nan print as N/A. One observation has a minimum, mean and maximum
    (5.00 each) but no spread, so St.Dev and SEM are N/A; no observations at
    all gives five N/A cells."""
    from raptor.core.engine.extended_results import _number, _stat_row

    assert _number(float("inf"), 2) == "N/A" and _number(float("nan"), 2) == "N/A"
    row = _stat_row("Label", [5.0], 2)          # one value: no St.Dev / SEM
    assert row.split()[1:4] == ["5.00"] * 3 and row.split()[-2:] == ["N/A", "N/A"]
    assert _stat_row("Label", [], 2).split()[1:] == ["N/A"] * 5


def test_extended_section_notes_censored_mttff() -> None:
    """One of two runs never failed, so its MTTFF is the run length (a lower
    bound) and the section says so: '1 of 2 run(s) ...'."""
    from raptor.core.engine.extended_results import build_extended_results_section

    lines = build_extended_results_section([_Run(censored=True), _Run()])
    note = next(x for x in lines if x.startswith("MTTFF:"))
    assert "1 of 2 run(s)" in note and "lower bound" in note


def test_criticality_table_empty_when_no_block_data() -> None:
    """A run with no criticality data prints the placeholder line instead of
    an empty table."""
    from raptor.core.engine.extended_results import build_extended_results_section

    lines = build_extended_results_section([_Run()])
    assert "No block criticality data." in lines


def test_criticality_table_orders_by_failure_share_and_hints_missing_data() -> None:
    """Shares are fractions shown as %: High 0.8 -> 80.00, Low 0.2 -> 20.00.
    Rows sort by failure share descending, a block with no failure share
    (N/A) last. No block has downtime or cost shares, so both hints print."""
    from raptor.core.engine.extended_results import build_extended_results_section

    crit = {
        1: {"name": "Low", "failure": 0.2, "downtime": None, "cost": None},
        2: {"name": "High", "failure": 0.8, "downtime": None, "cost": None},
        3: {"name": "NoData", "failure": None, "downtime": None, "cost": None},
    }
    lines = build_extended_results_section([_Run(criticality=crit)])
    i = lines.index("Block_Criticality")
    rows = [x for x in lines[i:] if x[:4] in ("High", "Low ", "NoDa")]
    assert [r.split()[0] for r in rows] == ["High", "Low", "NoData"]
    assert element_of(rows[0]) == ("block", 2)
    assert "80.00" in rows[0] and "20.00" in rows[1] and "N/A" in rows[2]
    assert "Downtime shares need Weak Link analysis. Cost shares need Cost analysis." in lines


def test_criticality_hint_only_for_the_missing_share() -> None:
    """Downtime share is present (0.5 -> 50.00), cost is not: only the Cost
    hint is printed, never the Weak Link one."""
    from raptor.core.engine.extended_results import build_extended_results_section

    crit = {1: {"name": "A", "failure": 1.0, "downtime": 0.5, "cost": None}}
    lines = build_extended_results_section([_Run(criticality=crit)])
    assert "Cost shares need Cost analysis." in lines
    assert not any("Weak Link" in x for x in lines)


# ------------------------------------------------------------ runner permits

def test_run_permits_gate_concurrent_runs_and_honour_stop(monkeypatch) -> None:
    """Two cores' worth of permits shared by the chunk workers.

    With 1 permit: the first take succeeds at once and leaves 0; with the
    Stop flag already set, a second take (which would otherwise wait) gives
    up and returns False; giving the permit back lets a take succeed again.
    With no permit pool (a single process) every take succeeds."""
    import threading

    from raptor.core.engine import runner

    monkeypatch.setattr(runner, "_PERMITS", None)
    assert runner._take_run_permit() is True
    runner._give_run_permit()                   # a no-op without a pool

    stop = threading.Event()
    monkeypatch.setattr(runner, "_PERMITS", threading.Semaphore(1))
    monkeypatch.setattr(runner, "_STOP_FLAG", stop)
    assert runner._take_run_permit() is True    # 1 -> 0
    stop.set()
    assert runner._take_run_permit() is False   # would wait; Stop wins
    runner._give_run_permit()                   # 0 -> 1
    assert runner._take_run_permit() is True

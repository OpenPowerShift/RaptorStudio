"""The per-run cost/time zeroing has one owner (review E-03).

`BlockStatistics`' 19-field cost-and-time reset was written out **twice** — at
kickoff (`kickoff.py`) and at the mid-run statistics reset
(`orchestrator.reset_all_statistics`) — as six chained-assignment statements.

**The finding as written said the two sites "must agree". The execution trace
in §2 corrected that, and it matters here.** They are not two competing copies
of one rule applied at alternative times: they are *two different instants of
one reset*, and **both fire in a single run**. A `CycleSimStartStatsCapacityProbe`
model (term=CycleSim, stats_start=5) reaches `kickoff`'s reset at t≈120.04 and
the orchestrator's at t≈119.87.

So the hazard is not "the copies disagree today". It is that the field list is
duplicated, and **each site is correct in isolation** — so no amount of running
either one detects a field the other has stopped resetting. Add a cost or time
accumulator and run N+1's cost table silently accumulates on top of run N's.
`kickoff.py`'s own comment says exactly that, in the imperative.

The fix derives the list from the dataclass, so the two lists that must agree
cannot exist as two.

**The weak-analysis fields are deliberately NOT in the reset**, and this is the
part most likely to be "tidied up" by a later pass:

- `kickoff` sets `mission_bool = True`, `m_completed = 0` **unconditionally**.
- `orchestrator` sets them from the block's *current* status, gated on
  `if self.model.weak_analysis`.

Two different rules. §2 traced the first as *more* faithful (with `weakAnalysis`
off, `mission_bool` is written 10–32× and read zero times), so folding them
into the helper would have picked a winner by accident.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raptor.core.engine.blocks import BlockStatistics, cost_and_time_fields

REPO = Path(__file__).resolve().parent.parent.parent
ENGINE = REPO / "raptor" / "core" / "engine"

#: The 19 fields the two hand-written resets covered, listed independently so
#: a change to the derivation is caught rather than absorbed by it.
EXPECTED = {
    "idle_cost", "hold_cost", "rep_cost", "op_cost", "sb_cost",
    "pm_hold_cost", "pm_cost", "done_cost", "disp_cost", "sp_cost",
    "es_cost", "run_time", "repair_time", "rep_hold_time", "pm_time",
    "pm_hold_time", "idle_time", "standby_time", "done_time",
}


class TestTheDerivation:
    def test_it_covers_exactly_the_nineteen_fields(self):
        assert set(cost_and_time_fields()) == EXPECTED

    def test_every_one_is_a_float(self):
        fields = BlockStatistics.__dataclass_fields__
        for name in cost_and_time_fields():
            assert fields[name].type == "float", (
                f"{name} is {fields[name].type}; reset_cost_and_time zeroes "
                "floats only, so a non-float accumulator needs a deliberate "
                "decision at its call site")

    def test_the_weak_analysis_fields_are_excluded(self):
        got = set(cost_and_time_fields())
        assert "mission_bool" not in got
        assert "m_completed" not in got

    def test_the_exclusion_is_by_name_and_not_only_by_type(self):
        """The subtlety, found by trying to fold them in.

        `mission_bool` is a `bool` and `m_completed` an `int`, so the
        `f.type == "float"` filter already excludes both — removing the
        explicit `_WEAK_ANALYSIS_FIELDS` check changes NOTHING observable,
        and a test asserting only "they are absent" passes straight through
        the mutation.

        The explicit exclusion is still right: it records *why* those two
        fields are out of scope, so the day one of them becomes a float the
        decision is deliberate rather than accidental. This test asserts the
        exclusion is **declared**, not merely **effective**.
        """
        import inspect

        from raptor.core.engine import blocks
        src = inspect.getsource(blocks.cost_and_time_fields)
        assert "_WEAK_ANALYSIS_FIELDS" in src, (
            "the explicit exclusion was removed. It is currently redundant "
            "with the float filter, but it is what documents the decision -- "
            "and it becomes load-bearing the moment either field changes "
            "type.")
        assert frozenset(
            {"mission_bool", "m_completed"}) == blocks._WEAK_ANALYSIS_FIELDS, (
            "the declared exclusion set changed; the two weak-analysis "
            "fields have deliberately different rules at their two call "
            "sites and must not be swept into the shared reset")

    def test_a_new_float_field_is_picked_up_automatically(self):
        """The whole point of deriving rather than listing. Simulated by
        asking: is any float field missing from the tuple?"""
        floats = {n for n, f in BlockStatistics.__dataclass_fields__.items()
                  if f.type == "float"}
        assert floats == set(cost_and_time_fields()), (
            f"not covered: {floats - set(cost_and_time_fields())}")


class TestResetCostAndTime:
    def test_it_zeroes_everything(self):
        bs = BlockStatistics()
        for name in cost_and_time_fields():
            setattr(bs, name, 7.5)
        bs.reset_cost_and_time()
        for name in cost_and_time_fields():
            assert getattr(bs, name) == 0.0, f"{name} survived the reset"

    def test_it_leaves_the_weak_analysis_fields_alone(self):
        """A caller that set them deliberately must not have them clobbered
        by a cost reset -- both call sites write them immediately after."""
        bs = BlockStatistics()
        bs.mission_bool = False
        bs.m_completed = 9
        bs.reset_cost_and_time()
        assert bs.mission_bool is False
        assert bs.m_completed == 9

    def test_it_is_idempotent(self):
        """The orchestrator's sweep can run mid-run on an already-reset
        object."""
        bs = BlockStatistics()
        for name in cost_and_time_fields():
            setattr(bs, name, 3.0)
        bs.reset_cost_and_time()
        bs.reset_cost_and_time()
        assert all(getattr(bs, n) == 0.0 for n in cost_and_time_fields())


class TestOneOwner:
    SITES = ["kickoff.py", "orchestrator.py"]

    @pytest.mark.parametrize("rel", SITES)
    def test_no_site_lists_the_fields(self, rel):
        """AST over the whole module: a chained assignment to a `bs.*` cost
        field is the old inlined reset."""
        tree = ast.parse((ENGINE / rel).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in ("bs", "self.stats_by_block")
                        and target.attr in EXPECTED):
                    pytest.fail(
                        f"{rel}:{node.lineno} assigns {target.attr} inline; "
                        "call stats.reset_cost_and_time()")

    @pytest.mark.parametrize("rel", SITES)
    def test_both_sites_call_the_method(self, rel):
        assert "reset_cost_and_time()" in (ENGINE / rel).read_text()

    def test_the_method_is_defined_once(self):
        tree = ast.parse((ENGINE / "blocks.py").read_text())
        defs = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "reset_cost_and_time"]
        assert len(defs) == 1


class TestMissionFieldsStayWithCallers:
    """The two weak-analysis rules genuinely differ, and each belongs to its
    call site."""

    def test_kickoff_sets_them_unconditionally(self):
        """`blockMissionBool[i]:=TRUE; blockMCompleted[i]:=0` at every run
        start -- no `weakAnalysis` gate."""
        src = (ENGINE / "kickoff.py").read_text()
        i = src.index("reset_cost_and_time()")
        tail = src[i:i + 700]
        assert "bs.mission_bool = True" in tail
        assert "bs.m_completed = 0" in tail
        # Not gated.
        assert "weak_analysis" not in tail.split("bs.mission_bool")[0]

    def test_orchestrator_gates_them_on_weak_analysis(self):
        src = (ENGINE / "orchestrator.py").read_text()
        i = src.index("reset_cost_and_time()")
        tail = src[i:i + 900]
        assert "if self.model.weak_analysis:" in tail
        assert "bs.mission_bool = block.op_status not in _MAINT_LIKE" in tail

    def test_the_two_rules_are_genuinely_different(self):
        """If they ever became the same, the exclusion would be unnecessary
        and worth revisiting -- so this says so rather than assuming."""
        kick = (ENGINE / "kickoff.py").read_text()
        orch = (ENGINE / "orchestrator.py").read_text()
        assert "bs.mission_bool = True" in kick
        assert "bs.mission_bool = block.op_status not in _MAINT_LIKE" in orch


class TestBothInstantsAreCovered:
    """The trace finding, stated as a test: the two resets are two instants of
    one rule, not alternatives."""

    def test_both_sites_exist_and_are_different_functions(self):
        assert "def reset_all_statistics" in (ENGINE / "orchestrator.py").read_text()
        assert "reset_cost_and_time()" in (ENGINE / "kickoff.py").read_text()

    def test_the_event_twin_is_still_self_maintaining(self):
        """`EventStatistics` is zeroed by an `isinstance(fval, float)` sweep in
        the orchestrator, which **cannot** drift. Deliberately left alone --
        a sweep over `vars()` is self-maintaining by construction, so there is
        no duplicated list to consolidate."""
        src = (ENGINE / "orchestrator.py").read_text()
        assert "isinstance(fval, float)" in src
        assert "es.reset_cost_and_time" not in src, (
            "EventStatistics has no such method; its float sweep is "
            "self-maintaining and is deliberately NOT unified")
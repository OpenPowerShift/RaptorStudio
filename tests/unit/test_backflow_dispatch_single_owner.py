"""The per-dependent back-flow dispatch has one owner (review E-05).

`BackFlowDependencies`'s per-dependent body was written out **twice** in
`dependency.py`: once for the block/event-sourced view
(`back_flow_dependencies`) and once for the node-sourced one
(`back_flow_dependencies_from_node`). At 194 tokens it was the largest
within-file clone in the repo, and `dependency.py` was the most duplicated
engine file at 19.0%.

§2.4 of the review established that **the source does not have two copies**:
`BackFlowDependencies(IN changeType : NodeStatusType)` is a single routine
taking a discriminator, and the two Python copies differed only in the
*membership guard* that gets a block into the loop at all. So the fix is the
source's own shape — one traversal, with the guard left to the caller.

A mechanical diff of the two bodies found **every executable line
identical**, with three exceptions, all of which this test pins:

1. The node copy carried a **second, unreachable** `special_back_flow`
   block. The first (identical) one already handled the case and `continue`d,
   so the second could never run — orphaned when a `returning_from_maintenance` arm
   was inserted above it.
2. The `draw_action is None` error message differed; `origin` supplies the
   distinguishing words.
3. **Only the node copy had a trailing `else: raise EngineInvariantError`**
   for an unrecognised action; the block copy fell through silently.

**The third is a behaviour change**, in the safe direction: the block-sourced
path now raises on an action it does not recognise instead of doing nothing.
It is marked `# pragma: no cover` because no callback can produce one — so
this test asserts the guard *exists* by reading the AST rather than by
executing it.

One thing that is easy to get wrong when extracting a `transition` call out
of a loop, and that this extraction nearly did: **the reason argument is not
cosmetic.** `TransitionFn` is `(block, status, reason, sim_time)`, and the
reason becomes the `InterruptReason` and the log's "why" column. The
`returning_from_maintenance` arm passes **`""`**, not `"Dependence"`, because the
block is still in its fail-wait and has no new reason to record. A blanket
find-and-replace during the extraction broke exactly that, and the existing
tests caught it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from raptor.core.engine import dependency
from raptor.core.engine.dependency import (
    _dispatch_dependent_block,
    back_flow_dependencies,
    back_flow_dependencies_from_node,
)

REPO = Path(__file__).resolve().parent.parent.parent
DEP = REPO / "raptor" / "core" / "engine" / "dependency.py"


class TestOneOwner:
    def test_the_dispatch_is_defined_once(self):
        tree = ast.parse(DEP.read_text())
        defs = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "_dispatch_dependent_block"]
        assert len(defs) == 1

    @pytest.mark.parametrize("fn", [back_flow_dependencies,
                                    back_flow_dependencies_from_node])
    def test_both_views_call_it(self, fn):
        src = inspect.getsource(fn)
        assert "_dispatch_dependent_block(" in src, (
            f"{fn.__name__} still inlines the per-dependent dispatch")

    @pytest.mark.parametrize("fn", [back_flow_dependencies,
                                    back_flow_dependencies_from_node])
    def test_neither_inlines_a_transition_anymore(self, fn):
        """AST: the callers legitimately *name* statuses in their guard
        clauses, so this looks for the transition call specifically."""
        tree = ast.parse(inspect.getsource(fn))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "transition"):
                pytest.fail(
                    f"{fn.__name__}:{node.lineno} calls transition directly; "
                    "the dispatch owns it")

    def test_the_membership_guard_stays_with_the_caller(self):
        """The whole point of §2.4: the guard is the *only* thing that
        differs, so it must remain in the callers."""
        block = inspect.getsource(back_flow_dependencies)
        assert "dep_block.dep_type != _depended_kind" in block
        node = inspect.getsource(back_flow_dependencies_from_node)
        assert "resolve_dependency_node_id(dep_block, end_node_id, own_node_id)" in node
        assert "if target_id != node.id:" in node

    def test_the_guard_is_not_moved_into_the_shared_body(self):
        """If it were, the two views would silently share one membership
        test — which is the bug the duplication was avoiding."""
        src = inspect.getsource(_dispatch_dependent_block)
        assert "resolve_dependency_node_id" not in src
        assert "dep_type" not in src
        assert "_depended_kind" not in src


class TestTheUnreachableBranchIsGone:
    """Exception 1. The orphaned second `special_back_flow` block."""

    def test_there_is_exactly_one_special_back_flow_branch(self):
        tree = ast.parse(inspect.getsource(_dispatch_dependent_block))
        hits = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.If)
                and isinstance(n.test, ast.Name)
                and n.test.id == "special_back_flow"]
        assert len(hits) == 1, (
            f"expected one special_back_flow branch, found {len(hits)} at "
            f"{hits}. The second was unreachable (the first `continue`d) and "
            "was orphaned when a returning_from_maintenance arm was inserted above it.")

    def test_it_is_reachable(self):
        """The surviving branch must be reachable: no *unconditional* exit
        may sit between the Down arm's entry and it.

        Asserted on the AST rather than by splitting the source on a string,
        because `if change_type == "Down":` also occurs inside the docstring
        -- which is how my first version of this test "found" an early
        return that was not on the path at all.
        """
        tree = ast.parse(inspect.getsource(_dispatch_dependent_block))
        fn = tree.body[0]
        down = next(n for n in fn.body
                    if isinstance(n, ast.If)
                    and ast.unparse(n.test) in ("change_type == 'Down'",
                                                'change_type == "Down"'))
        # Walk down through guard-only statements, looking for a bare Return.
        node = down
        while True:
            if isinstance(node, ast.Return):
                pytest.fail("the Down arm returns unconditionally before the "
                            "special_back_flow branch")
            inner = node.body[1:]
            if not inner:
                break
            head = inner[0]
            if not (isinstance(head, ast.If)
                    and isinstance(head.test, ast.Compare)):
                break
            node = head
        # And the branch itself must be reachable from here.
        reachable = [n for n in ast.walk(down)
                     if isinstance(n, ast.If)
                     and isinstance(n.test, ast.Name)
                     and n.test.id == "special_back_flow"]
        assert reachable, "the special_back_flow branch is gone entirely"


class TestTheTrailingRaise:
    """Exception 3, and the one behaviour change."""

    def test_an_unrecognised_action_raises(self):
        """The node copy always had this; the block copy silently fell
        through. The shared body keeps it, so the block path is now strict
        too."""
        src = inspect.getsource(_dispatch_dependent_block)
        tree = ast.parse(src)
        raises_on = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Raise)]
        assert any("action" in ast.unparse(n) for n in raises_on), (
            "the shared dispatch no longer raises on an unrecognised action")

    def test_it_is_marked_no_cover_because_it_is_unreachable(self):
        """No callback can produce an action outside the four handled, so
        this is a guard, not a path. Marked rather than tested by execution."""
        src = inspect.getsource(_dispatch_dependent_block)
        assert "pragma: no cover" in src, (
            "the unreachable-action raise needs its pragma; otherwise it "
            "shows up as an uncovered line in every run")


class TestTheTransitionReason:
    """The argument that is easy to lose in an extraction like this one.

    `TransitionFn` is `(block, status, reason, sim_time)` and the reason
    becomes the `InterruptReason` plus the log's "why" column. Most arms pass
    `"Dependence"`; the `returning_from_maintenance` arm passes **`""`** on purpose,
    because the block is still in its fail-wait and has no new reason.
    """

    def _calls(self):
        tree = ast.parse(inspect.getsource(_dispatch_dependent_block))
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "transition_dep"]

    def test_every_call_passes_a_reason(self):
        calls = self._calls()
        assert calls, "no transition_dep calls found -- extraction lost them"
        for c in calls:
            assert len(c.args) == 4, (
                f"line {c.lineno}: transition_dep takes "
                "(block, status, reason, sim_time); got {len(c.args)} args")

    def test_the_return_from_maint_arm_passes_an_empty_reason(self):
        """The subtle one. `"Dependence"` here would tell the scheduler the
        block entered Idle *because of a dependency*, when in fact it is
        still mid-repair-completion and only looks Idle.

        Located on the AST: the arm is the `if` that tests
        `dep_block.run_state.returning_from_maintenance` AND contains a `no_wake_once`
        assignment. There are two such tests in the body (the special-
        back-flow one and this one), so the discriminator is required --
        matching on the test alone picks the wrong branch.
        """
        tree = ast.parse(inspect.getsource(_dispatch_dependent_block))
        arms = [n for n in ast.walk(tree)
                if isinstance(n, ast.If)
                and "returning_from_maintenance" in ast.unparse(n.test)
                and any("no_wake_once" in ast.unparse(a)
                        for a in ast.walk(n))]
        assert len(arms) == 1, (
            f"expected one no_wake_once arm, found {len(arms)}")
        reasons = [ast.literal_eval(c.args[2]) for c in ast.walk(arms[0])
                   if isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Name)
                   and c.func.id == "transition_dep"]
        assert reasons == ["", ""], (
            f"the returning_from_maintenance arm passes {reasons}; the source passes "
            "an EMPTY reason because the block is still in its fail-wait")

    def test_the_other_arms_pass_dependence(self):
        src = inspect.getsource(_dispatch_dependent_block)
        assert src.count('"Dependence", sim_time') == 5, (
            "the Standby, specialBackFlow, GoIdle and AllUp arms all pass "
            '"Dependence" (5 call sites total)')


class TestOriginOnlyWordsTheError:
    """Exception 2. `origin` exists for the message and nothing else."""

    def test_both_callers_pass_an_origin(self):
        for fn in (back_flow_dependencies, back_flow_dependencies_from_node):
            assert "origin=" in inspect.getsource(fn)

    def test_the_two_messages_stay_distinguishable(self):
        block = inspect.getsource(back_flow_dependencies)
        node = inspect.getsource(back_flow_dependencies_from_node)
        origins = [ln for ln in block.splitlines() if "origin=" in ln]
        origins += [ln for ln in node.splitlines() if "origin=" in ln]
        texts = [o.split("origin=")[1] for o in origins]
        assert len(set(texts)) == len(texts), (
            f"the two views pass the same origin text: {texts}")

    def test_origin_is_not_used_for_control_flow(self):
        """If `origin` ever gated behaviour, the two views would diverge
        again -- which is the whole thing this refactor removed."""
        src = inspect.getsource(_dispatch_dependent_block)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                names = {n.id for n in ast.walk(node.test)
                         if isinstance(n, ast.Name)}
                assert "origin" not in names, (
                    f"line {node.lineno} branches on origin")


class TestBehaviourIsPreserved:
    """The two arms that behave differently, checked as behaviour."""

    def test_a_drawless_down_still_raises_with_the_caller_named(self):
        from raptor.core.model import BlockStatus, NodeStatus, RBDBlock, RBDNode

        # `end_node_id` MUST equal the node's id: `dependency_num=-2` is
        # `sysDepend`, which `resolve_dependency_node_id` resolves to the
        # model's End node. With a mismatched `end_node_id` the membership
        # guard skips the block entirely and the raise is never reached --
        # which is what my first version of this test did, and it passed
        # only because nothing raised.
        node = RBDNode(id=1, status=NodeStatus.DOWN)
        # BOTH must defeat the deterministic shortcut: the arm tests
        # `dep_idle_percent == 100.0 OR def_dep_state_idle`, so a non-100
        # percentage alone is not enough while `def_dep_state_idle` keeps
        # its default True.
        block = RBDBlock(name="d", id=2, op_status=BlockStatus.RUNNING,
                         dependency_num=-2, dep_idle_percent=0.0,
                         def_dep_state_idle=False)
        with pytest.raises(Exception) as ei:
            back_flow_dependencies_from_node(
                "Down", node,
                dependency.BackFlowDeps(
                    end_node_id=1, all_blocks=[block], links_by_id={},
                    transition=lambda *a: None),
                0.0)
        assert "node-triggered" in str(ei.value)

    def test_an_unhandled_action_is_rejected_not_swallowed(self):
        """The behaviour change from exception 3, exercised directly on the
        shared body rather than through a whole run."""
        from raptor.core.model import BlockStatus, RBDBlock

        block = RBDBlock(name="d", id=2, op_status=BlockStatus.RUNNING,
                         dependency_num=-2, dep_idle_percent=0.0)
        with pytest.raises(Exception) as ei:
            _dispatch_dependent_block(
                block, "Down", lambda *a: None, 0.0,
                special_back_flow=False, draw_action=lambda b, t: "Bogus",
                induce=None, origin="test")
        assert "Bogus" in str(ei.value)


class TestTheSourceShape:
    """§2.4's claim, restated as something a test can hold."""

    def test_the_source_parameterises_on_change_type_not_on_guard(self):
        """`BackFlowDependencies(IN changeType : NodeStatusType)` — one
        routine. The Python now has one traversal keyed on `change_type`,
        with the guard in the caller, which is the same shape."""
        from raptor.core.engine.dependency import DependencyTrigger
        assert set(DependencyTrigger.__args__) == {"Down", "AllUp", "NodeStandby"}

    def test_the_dispatch_switches_on_change_type(self):
        """Three top-level arms, each keyed on `change_type == <literal>`.

        Checked at the top level of the function body only: an inner `==`
        on something else is not a change-type dispatch.
        """
        def key_of(node):
            test = node.test
            if (isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "change_type"
                    and len(test.comparators) == 1
                    and isinstance(test.comparators[0], ast.Constant)):
                return test.comparators[0].value
            return None

        tree = ast.parse(inspect.getsource(_dispatch_dependent_block))
        fn = tree.body[0]
        keyed = {}
        # Top-level arms...
        for node in fn.body:
            if isinstance(node, ast.If) and (k := key_of(node)):
                keyed[k] = node
        # ...and any chained as an `elif`, which is an `ast.If` inside the
        # previous arm's `orelse`. `AllUp` is reached that way.
        for node in ast.walk(fn):
            if isinstance(node, ast.If) and (k := key_of(node)):
                keyed.setdefault(k, node)
        assert set(keyed) == {"Down", "AllUp", "NodeStandby"}, (
            f"the dispatch has arms for {set(keyed)}, not all three "
            "change types")


def test_no_executable_line_was_added():
    """The honest version of "did this shrink the file".

    `dependency.py` grew by a handful of lines, because the extraction kept
    the **union** of both copies' source citations rather than picking one.
    The claim worth pinning is that the *executable* body shrank by roughly
    the size of the duplicate, and that the duplication is gone -- which
    `TestOneOwner` and `TestTheUnreachableBranchIsGone` already assert.

    So this asserts the direction and the scale, not an exact line count:
    a big jump means the body was pasted back rather than extracted.
    """
    src = DEP.read_text()
    lines = len(src.splitlines())
    # 1,120 until `index_node_dependents` (about 40 lines, a per-run index
    # that replaced a scan of every block per event) was added.
    assert lines < 1170, (
        f"dependency.py is {lines} lines; it was 1,048 before E-05, so this "
        "is not an extraction any more")

    # The dead duplicate's own fingerprint: the `specialBackFlow` branch was
    # ported TWICE. Counted in the shared body, not in the file:
    # `resend_back_flow` has its own (correct) copy of the test.
    body = inspect.getsource(_dispatch_dependent_block)
    assert body.count("if special_back_flow:") == 1, (
        "the specialBackFlow branch appears more than once in the shared "
        "body -- the unreachable duplicate is back")

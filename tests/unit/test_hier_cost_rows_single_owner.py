"""The hierarchy cost rollup has one owner (review E-04).

`core/engine/costs.py` had two cost-aggregation entry points —
`build_cost_run_report` (the final report) and `interim_cost_snapshot`
(the mid-run figure) — and each carried its own copy of the 19-line
hierarchy rollup. They were identical except for a comment and one line:
where `events_total` was summed.

Two entry points that must agree on the *category set* and the *ordering*,
with nothing enforcing it. `costs.py` was 21% duplicated, the highest rate
in the repo, and this was most of it.

`build_hier_cost_rows(model, blocks, events)` now owns it. Each caller keeps
computing its own totals (`pools_total`, `resources_total`,
`events_total`) — those genuinely differ and are that caller's business.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


from raptor.core.engine.costs import build_hier_cost_rows
from raptor.core.engine.reporting import BlockCostRow, EventCostRow

REPO = Path(__file__).resolve().parent.parent.parent


def _model(hiers: dict[int, int], block_pairs=()) -> SimpleNamespace:
    """`hiers` maps hier id -> parent id (0 = top level)."""
    by_id = {hid: SimpleNamespace(id=hid, name=f"H{hid}", parent_id=pid)
             for hid, pid in hiers.items()}
    blocks = {b.id: b for b, _row in block_pairs}
    return SimpleNamespace(hiers_by_id=by_id, blocks_by_id=blocks,
                           events_by_id={})


def _block(bid: int, parent: int, purchase=0.0, operating=0.0, sparing=0.0,
           disposal=0.0, total=0.0):
    """A model block (id + parent_id) paired with its cost row.

    Two objects, because that is what the helper actually consumes: it walks
    `model.blocks_by_id` for the *membership* test and indexes `blocks[b.id]`
    for the amounts. The row itself carries no id."""
    return (SimpleNamespace(id=bid, parent_id=parent),
            BlockCostRow(name=f"b{bid}", init_spare_cost=0.0,
                         new_spares_cost=0.0, emer_ship_cost=0.0,
                         purchase=purchase, operating=operating,
                         sparing=sparing, disposal=disposal, total=total))


def _event(eid: int, parent: int, total=0.0):
    """An EventCostRow is self-describing (it carries `parent_id`), so the
    helper's `ev.parent_id` test reads the ROW, not the model object."""
    return eid, EventCostRow(name=f"e{eid}", initial=0.0, success=0.0,
                             failure=0.0, total=total, parent_id=parent)


def _run(hiers: dict[int, int], block_pairs=(), event_pairs=()):
    """Build the model + row dicts the helper consumes, then run it."""
    model = _model(hiers, block_pairs)
    blocks = {b.id: row for b, row in block_pairs}
    events = dict(event_pairs)
    return build_hier_cost_rows(model, blocks, events)


class TestAccumulation:
    def test_a_block_is_charged_to_its_own_hierarchy(self):
        rows = _run({1: 0}, [_block(10, 1, purchase=5.0, total=5.0)])
        assert rows[1].purchase == 5.0
        assert rows[1].total == 5.0

    def test_an_event_is_charged_to_its_own_hierarchy(self):
        rows = _run({1: 0}, (), [_event(20, 1, total=7.0)])
        assert rows[1].events == 7.0
        assert rows[1].total == 7.0

    def test_all_five_categories_are_carried(self):
        """THE POINT of the finding: two copies that must agree on the
        CATEGORY SET. A category added to one and not the other would
        report a different hierarchy total depending on which entry point
        produced it."""
        row = _run({1: 0},
                   [_block(10, 1, purchase=1.0, operating=2.0, sparing=3.0,
                           disposal=4.0, total=10.0)],
                   [_event(20, 1, total=5.0)])[1]
        assert (row.purchase, row.operating, row.sparing, row.disposal,
                row.events, row.total) == (1.0, 2.0, 3.0, 4.0, 5.0, 15.0)

    def test_a_block_with_no_hierarchy_is_not_counted(self):
        rows = _run({1: 0}, [_block(10, 0, total=99.0)])
        assert rows[1].total == 0.0, (
            "a top-level block belongs to no hierarchy row and must not "
            "leak into one")

    def test_a_model_with_no_hierarchies_gives_no_rows(self):
        assert _run({}) == {}


class TestOwnSnapshot:
    def test_own_is_the_pre_nesting_figure(self):
        """`hc.own` is snapshotted BEFORE nested hierarchies fold in, which
        is what lets a caller report a hierarchy's direct costs."""
        rows = _run({1: 0, 2: 1},
                    [_block(10, 1, total=100.0), _block(11, 2, total=5.0)])
        assert rows[2].own[-1] == 5.0, "the child's own total is just itself"
        assert rows[1].own[-1] == 100.0, (
            "the parent's own total must EXCLUDE the child")
        assert rows[1].total == 105.0, "but the rolled-up total includes it"


class TestNestedRollupStillApplies:
    def test_a_child_folds_into_its_parent(self):
        rows = _run({1: 0, 2: 1},
                    [_block(10, 1, total=100.0), _block(11, 2, total=5.0)])
        assert rows[1].total == 105.0
        assert rows[2].total == 5.0

    def test_three_levels_fold_deepest_first(self):
        """Ordering matters: a grandchild must reach the top-level row in
        one pass, which requires processing deepest-first."""
        rows = _run({1: 0, 2: 1, 3: 2},
                    [_block(10, 1, total=1.0), _block(11, 2, total=10.0),
                     _block(12, 3, total=100.0)])
        assert rows[1].total == 111.0
        assert rows[2].total == 110.0
        assert rows[3].total == 100.0

    def test_every_category_folds_not_just_total(self):
        row = _run({1: 0, 2: 1},
                   [_block(10, 1, purchase=1.0, operating=2.0, sparing=3.0,
                           disposal=4.0, total=10.0),
                    _block(11, 2, purchase=10.0, operating=20.0,
                           sparing=30.0, disposal=40.0, total=100.0)],
                   [_event(20, 2, total=1000.0)])[1]
        assert (row.purchase, row.operating, row.sparing, row.disposal,
                row.events) == (11.0, 22.0, 33.0, 44.0, 1000.0)

    def test_seq_is_the_iteration_order(self):
        rows = _run({7: 0, 3: 0, 5: 0})
        assert [rows[h].seq for h in (7, 3, 5)] == [1, 2, 3]


class TestBothEntryPointsAgree:
    """The finding's substance: two aggregation entry points that must agree.

    Stated as agreement rather than as "there is only one implementation", so
    a future divergence fails as a wrong number instead of as a structural
    nit."""

    def test_the_interim_and_final_paths_agree_on_every_category(self):
        """`interim_cost_snapshot` and `build_cost_run_report` both build
        hierarchy rows. If one summed a category the other skipped, the
        interim figure and the end-of-run table would disagree -- which is
        exactly what E-04's duplicated body allowed."""
        import inspect

        from raptor.core.engine import costs
        # Both entry points must reach the rows through the one owner.
        for fn in (costs.build_cost_run_report, costs.interim_cost_snapshot):
            body = inspect.getsource(fn)
            assert "build_hier_cost_rows(" in body, (
                f"{fn.__name__} builds hierarchy rows itself")

    def test_the_helper_accumulates_all_five_categories(self):
        """The category set, pinned on the owner itself.

        `disposal` is the one most easily missed: it is the only category
        that is not a block's initial or running cost, so a helper that
        summed the "obvious" four would look correct on most models.
        """
        import inspect

        from raptor.core.engine.costs import build_hier_cost_rows
        src = inspect.getsource(build_hier_cost_rows)
        for cat in ("purchase", "operating", "sparing", "disposal", "events"):
            assert f"hier_cost_row.{cat} +=" in src, (
                f"build_hier_cost_rows no longer accumulates {cat}")


class TestOneOwner:
    """Structural. Two identical copies are indistinguishable from one by
    behaviour, which is exactly how this survived."""

    COSTS = REPO / "raptor" / "core" / "engine" / "costs.py"

    def test_the_rollup_body_exists_once(self):
        """Counted as the accumulation statements, not as text: the module
        legitimately *names* the fields in the function that owns them."""
        tree = ast.parse(self.COSTS.read_text())
        builders = [n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "build_hier_cost_rows"]
        assert len(builders) == 1, "build_hier_cost_rows is defined twice"

        # The tell-tale of the old copy: a loop body that adds five
        # category fields to a freshly built HierCostRow.
        def adds_categories(fn) -> bool:
            adds = [n for n in ast.walk(fn)
                    if isinstance(n, ast.AugAssign)
                    and isinstance(n.target, ast.Attribute)
                    and n.target.attr in ("purchase", "operating", "sparing",
                                          "disposal", "events")]
            return len(adds) >= 5

        # `rollup_nested_hier_costs` legitimately adds the same five
        # fields -- it is the FOLD of children into parents, which is a
        # different job with its own tests and its own ordering rule. What
        # must not exist is the *accumulation*: walking
        # `model.blocks_by_id` / `events` to build a fresh row per
        # hierarchy, then snapshotting `own`.
        def accumulates_from_model(fn) -> bool:
            walks = any(
                isinstance(n, ast.Attribute)
                and n.attr in ("blocks_by_id", "events_by_id")
                for n in ast.walk(fn))
            snapshots_own = any(
                isinstance(n, ast.Attribute) and n.attr == "own"
                for n in ast.walk(fn))
            return walks and snapshots_own and adds_categories(fn)

        others = [n.name for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name != "build_hier_cost_rows"
                  and accumulates_from_model(n)]
        assert not others, (
            f"the hierarchy accumulation is inlined again in {others}; "
            "call build_hier_cost_rows. (rollup_nested_hier_costs is exempt: "
            "it folds children into parents, which is a different job.)")

    def test_both_entry_points_call_it(self):
        tree = ast.parse(self.COSTS.read_text())
        for fn in ("build_cost_run_report", "interim_cost_snapshot"):
            body = [n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == fn]
            assert body, f"{fn} is gone"
            called = {c.func.id for c in ast.walk(body[0])
                      if isinstance(c, ast.Call)
                      and isinstance(c.func, ast.Name)}
            assert "build_hier_cost_rows" in called, (
                f"{fn} builds its own hierarchy rows")

    def test_each_caller_keeps_its_own_totals(self):
        """`pools_total`/`resources_total`/`events_total` are the callers'
        own business and must NOT have been pulled into the shared helper
        by over-eager consolidation."""
        # AST, not text: the helper's DOCSTRING names these three while
        # explaining that they deliberately stay with their callers. A
        # substring check flags the explanation -- which is what my first
        # version did.
        import inspect
        tree = ast.parse(inspect.getsource(build_hier_cost_rows))
        assigned = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assigned.add(node.id)
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        assigned.add(t.id)
        for name in ("pools_total", "resources_total", "events_total"):
            assert name not in assigned, (
                f"{name} leaked into build_hier_cost_rows; it belongs to the "
                "caller that reports it")


class TestStillDelegatesToRollup:
    def test_build_hier_cost_rows_calls_the_nested_rollup(self):
        """`rollup_nested_hier_costs` remains its own function -- it has its
        own tests and its own ordering rule. This only guarantees the new
        owner still defers to it rather than re-implementing the fold."""
        import inspect

        src = inspect.getsource(build_hier_cost_rows)
        assert "rollup_nested_hier_costs(hiers, model)" in src

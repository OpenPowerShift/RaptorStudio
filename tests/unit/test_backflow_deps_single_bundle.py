"""The node-sourced back-flow bundle has one owner (review E-09).

`back_flow_dependencies_from_node` took a **ten-parameter** bundle, and
**seven** call sites assembled it by hand from the same orchestrator state.
Four of those sites spelled out the identical eight expressions:

    end_node.id if end_node else -1   self._all_blocks
    model.links_by_id                 _dispatch_dependency
    _draw_dependency_action            _induce_dependency
    model.nodes_by_id                 run_cold_logic_now=lambda ...

`BackFlowDeps` is now the bundle, built once by
`orchestrator._Cascade.backflow_deps()` and passed whole.

**The review's stated evidence was wrong on two counts**, and both mattered:

1. It named **two** call sites. There are **seven** — two in `cascade_cold`,
   two in `kickoff`, one in `orchestrator`, plus `resend_back_flow` and the
   module's own internal `_recurse_node_backflow` forwarder. So there were
   eight places to forget an argument, not two.
2. It listed **ten keyword arguments** (`hiers_by_id`, `interrupt_link`,
   `backflow_all_up`, `backflow_down`, `emit_log`, …). Those are arguments to
   **`run_cold_logic`**, a different call. Conflating the two is how the
   review came to a site count that understated the problem by 3.5×.

The `run_cold_logic` call in `cascade_cold` still hand-rolls a long keyword
bundle, and it is deliberately **left alone** — it is one site, and
consolidating it on the strength of a miscount would be the wrong fix.

`sim_time` is not in the bundle: it changes per call, and `run_cold_logic_now`
was a closure over it. The bundle stores the raw two-argument
`apply_cold_logic` and `cold_logic_now(sim_time)` builds the closure.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raptor.core.engine.dependency import BackFlowDeps

REPO = Path(__file__).resolve().parent.parent.parent

ENGINE = REPO / "raptor" / "core" / "engine"

#: Every module that calls the node-sourced back-flow.
CALLER_MODULES = [
    "cascade_cold.py",
    "kickoff.py",
    "orchestrator.py",
    "dependency.py",
]


def _call_sites(rel: str) -> list[ast.Call]:
    tree = ast.parse((ENGINE / rel).read_text())
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "back_flow_dependencies_from_node"
    ]


class TestOneOwner:
    def test_every_call_site_passes_the_bundle(self):
        """The signature is now four parameters. Any site still hand-listing
        the old bundle would be a `TypeError` at runtime, so this is
        asserted structurally too — it names the failure directly."""
        for rel in CALLER_MODULES:
            for call in _call_sites(rel):
                assert len(call.args) == 4, (
                    f"{rel}:{call.lineno} passes {len(call.args)} positional "
                    "args; expected (change_type, node, deps, sim_time)")
                assert not call.keywords, (
                    f"{rel}:{call.lineno} still uses keyword arguments "
                    f"{[k.arg for k in call.keywords]}; the bundle replaces "
                    "the whole keyword list")

    def test_there_are_seven_sites_not_two(self):
        """The review said two. Pinning the real count means a future call
        site is visible in a diff rather than hidden inside a refactor."""
        total = sum(len(_call_sites(rel)) for rel in CALLER_MODULES)
        assert total == 7, (
            f"expected 7 node-sourced back-flow call sites, found {total}")

    def test_the_external_sites_use_the_factory(self):
        """`cascade_cold`, `kickoff` and `orchestrator` all receive the
        cascade/orchestrator as `self` (or `cascade`), so none of them
        should be naming its internals.

        Two legitimate shapes: the bundle is the factory call itself
        (`cascade.backflow_deps()`), or it is a local that was assigned the
        factory call earlier in the same function -- which is what
        `cascade_cold` does, binding `deps` once and sharing it between its
        two back-flow arms. Both are the factory; only the second needed
        thinking about, and my first version of this test flagged it as a
        violation until I looked at why it was written that way.
        """
        for rel in ("cascade_cold.py", "kickoff.py", "orchestrator.py"):
            src = (ENGINE / rel).read_text()
            tree = ast.parse(src)
            for call in _call_sites(rel):
                unparsed = ast.unparse(call)
                if "backflow_deps()" in unparsed:
                    continue
                # Otherwise the bundle must be a Name bound to the factory
                # somewhere in this module.
                bound = set()
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Assign)
                            and any(isinstance(t, ast.Name) for t in node.targets)
                            and any(
                                isinstance(c, ast.Call)
                                and isinstance(c.func, ast.Attribute)
                                and c.func.attr == "backflow_deps"
                                for c in ast.walk(node.value))):
                        bound.update(t.id for t in node.targets
                                     if isinstance(t, ast.Name))
                assert bound, (
                    f"{rel}:{call.lineno} does not use backflow_deps():\n"
                    f"{unparsed}")
                # The bundle may be passed directly (`deps`) or derived
                # from it -- `cascade_cold`'s Down arm flips
                # `special_back_flow` per call with
                # `replace(deps, special_back_flow=special)`, and rebuilding
                # the bundle there would undo the point of building it once.
                used = set()
                for a in call.args:
                    used.update(n.id for n in ast.walk(a)
                                if isinstance(n, ast.Name))
                assert used & bound, (
                    f"{rel}:{call.lineno} passes {used}, which does not "
                    f"derive from backflow_deps() ({bound})")
                # ...and the derivation must be a `replace`, not a rebuild.
                for a in call.args:
                    for n in ast.walk(a):
                        if isinstance(n, ast.Call) and getattr(
                                n.func, "id", getattr(n.func, "attr", None)
                        ) == "BackFlowDeps":
                            pytest.fail(
                                f"{rel}:{call.lineno} constructs a second "
                                "BackFlowDeps at the call site; use "
                                "dataclasses.replace on the bound one")

    def test_no_site_lists_the_orchestrator_internals_anymore(self):
        """The duplication itself: `self._all_blocks`,
        `_draw_dependency_action`, `_induce_dependency` and
        `_dispatch_dependency` next to a
        `back_flow_dependencies_from_node` call.

        Counted by proximity in the AST, not as a flat text search: these
        attributes are legitimately used elsewhere in all three modules.
        """
        for rel in CALLER_MODULES:
            tree = ast.parse((ENGINE / rel).read_text())
            for call in _call_sites(rel):
                # Walk up to the enclosing statement and look at its window.
                stmt = call
                while not isinstance(stmt, (ast.stmt,)) or True:
                    break
                line_no = call.lineno
                for token in ("_all_blocks", "_draw_dependency_action",
                              "_induce_dependency", "_dispatch_dependency"):
                    for node in ast.walk(tree):
                        if (isinstance(node, ast.Attribute)
                                and node.attr == token
                                and abs(node.lineno - line_no) <= 12):
                            pytest.fail(
                                f"{rel}:{node.lineno} names {token} within "
                                f"12 lines of the back-flow call at "
                                f"{line_no}; call self.backflow_deps()")
                break


class TestTheBundle:
    def _deps(self, **kw):
        base = dict(end_node_id=1, all_blocks=[], links_by_id={},
                    transition=lambda *a: None)
        base.update(kw)
        return BackFlowDeps(**base)

    def test_it_is_frozen(self):
        """Bundles get shared across call sites, so mutating one in place
        would leak into the rest of the run."""
        deps = self._deps()
        with pytest.raises(Exception):    # noqa: B017 - FrozenInstanceError
            deps.end_node_id = 2

    def test_special_back_flow_defaults_off(self):
        assert self._deps().special_back_flow is False

    def test_nodes_by_id_defaults_to_none(self):
        """`None` disables the node-dependents half
        (`_back_flow_node_dependents`), so the default is the conservative
        one: block-half only."""
        assert self._deps().nodes_by_id is None

    def test_no_cold_logic_means_no_callback(self):
        assert self._deps().cold_logic_now(5.0) is None

    def test_cold_logic_now_closes_over_the_instant(self):
        seen: list[tuple[int, float]] = []

        def apply(n, t):
            seen.append((n.id, t))

        deps = self._deps(apply_cold_logic=apply)
        deps.cold_logic_now(7.5)(type("N", (), {"id": 3})())
        assert seen == [(3, 7.5)], (
            "run_cold_logic_now must apply at THIS sim_time, not the one the "
            "bundle was built with")

    def test_cold_logic_now_rebinds_per_instant(self):
        """The reason `sim_time` is not in the bundle: one bundle, many
        instants."""
        seen: list[float] = []
        deps = self._deps(apply_cold_logic=lambda n, t: seen.append(t))
        n = type("N", (), {"id": 1})()
        deps.cold_logic_now(1.0)(n)
        deps.cold_logic_now(2.0)(n)
        assert seen == [1.0, 2.0]

    def test_replace_preserves_everything_else(self):
        """`cascade_cold`'s Down arm flips `special_back_flow` without
        rebuilding the bundle, so the flip must not disturb the callbacks."""
        ran: list[float] = []
        deps = self._deps(apply_cold_logic=lambda n, t: ran.append(t))
        flipped = type(deps)(**{**{
            f: getattr(deps, f) for f in deps.__dataclass_fields__
            if f != "special_back_flow"}, "special_back_flow": True})
        assert flipped.special_back_flow is True
        assert flipped.nodes_by_id == deps.nodes_by_id
        assert flipped.transition is deps.transition
        flipped.cold_logic_now(3.0)(type("N", (), {"id": 1})())
        assert ran == [3.0]


class TestTheFactory:
    def test_it_binds_the_seven_orchestrator_values(self):
        """Every expression E-09 said the sites were duplicating."""
        from raptor.core.engine.dependency import BackFlowDeps as Deps
        import inspect

        from raptor.core.engine.orchestrator import _Cascade
        src = inspect.getsource(_Cascade.backflow_deps)
        for expr in ("end_node.id if self.end_node else -1",
                     "self._all_blocks",
                     "self.model.links_by_id",
                     "self._dispatch_dependency",
                     "self.model.nodes_by_id",
                     "self._draw_dependency_action",
                     "self._induce_dependency",
                     "self.apply_cold_logic"):
            assert expr in src, (
                f"backflow_deps no longer binds {expr}; a site that omitted "
                "it would silently lose the value")
        assert Deps is BackFlowDeps


class TestResendBackFlowIsUnchanged:
    """`resend_back_flow` builds its own bundle and does **not** forward
    `nodes_by_id`. That is preserved deliberately, and this test exists so a
    later reader does not "fix" it without investigating.

    Passing `nodes_by_id` would enable `_back_flow_node_dependents` — the
    NodeDepGroup half — during a resend, which is a real behaviour change
    (it would toggle `higherCold` on cold dependent nodes) and needs its own
    investigation against the source, not a drive-by in a refactor.
    """

    def test_resend_builds_an_explicit_bundle(self):
        src = (ENGINE / "dependency.py").read_text()
        assert "BackFlowDeps(" in src
        idx = src.index("def resend_back_flow(")
        body = src[idx:idx + 4000]
        assert "nodes_by_id=None" in body, (
            "resend_back_flow now forwards nodes_by_id -- that is a BEHAVIOUR "
            "change (it enables the NodeDepGroup half during a resend), not a "
            "refactor. Investigate it against Raptor 7's BackFlowDependencies first.")

    def test_the_node_half_is_gated_on_nodes_by_id(self):
        """The gate that makes the above true: `None` means the node-
        dependents half returns immediately."""
        from raptor.core.engine import dependency
        src = dependency._back_flow_node_dependents.__doc__ or ""
        assert src, "the node-dependents half lost its docstring"
        import inspect
        body = inspect.getsource(dependency._back_flow_node_dependents)
        assert "if nodes_by_id is None:" in body and "return" in body


class TestTheRecursionForwarder:
    def test_recurse_is_a_pure_forwarder(self):
        """`_recurse_node_backflow` used to re-list and unpack all ten
        parameters -- an eighth place to forget one. It must now only pass
        the bundle through."""
        import inspect

        from raptor.core.engine import dependency
        src = inspect.getsource(dependency._recurse_node_backflow)
        assert "back_flow_dependencies_from_node(" in src
        # No parameter unpacking left.
        for name in ("links_by_id", "all_blocks", "transition",
                     "special_back_flow", "draw_action"):
            assert f"{name}=" not in src, (
                f"_recurse_node_backflow re-lists {name}; it should forward "
                "the bundle untouched")
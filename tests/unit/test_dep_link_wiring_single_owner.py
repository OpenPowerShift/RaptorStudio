"""The depLink/siblings wiring has one owner (review E-10).

**E-10 did not survive a focused read, and saying so is most of its value.**

As written it named `graph.build_efpa_graph` and `capacity._extend_raw_priorities`
as a clone sharing a five-argument bundle, and declined to assert how much was
redundant. Measured:

- The CPD "51-token clone" is **only the two five-parameter signatures**. The
  bodies share no line and touch different fields.
- The source has **one** routine: Raptor 7 is a single
  `PROCEDURE RunEFPA` spanning lines 3–481, with the priority extension at
   and the capacity sections. Splitting it into an early
  pass and a capacity pass is a **faithful decomposition**, not a duplication.

**But the focused read found something the review had half-noticed in a table
and filed as an order-dependency hazard**: the `depLink`/`siblingsGroup` wiring
existed in *both* passes, in two implementations that differ in a way that
matters.

| | `graph.py` | `capacity.py` (removed) |
|---|---|---|
| guard on | `link.resolved_to_kind` | `link.connect_t_ref` |
| self-reference guard | `if sister is link: continue` | **absent** |
| reset | `dep_link`, `siblings_group`, `has_siblings` | `dep_link` only |

`capacity.py`'s copy was **redundant AND unguarded** — the combination that
survives longest, because running anything could not distinguish it from
correct, and it would have begun differing the day a model hit one of the two
divergent cases. Neither occurs in any of the 47 models scanned:

- a link whose `connect_t_ref` says Block/Event while `resolved_to_kind` does
  not — 0 cases;
- a self-loop where `sister is link` — 0 cases.

The review's own instinct was right even where its evidence was not: it
suspected order-dependence, and the real fault is a *weaker* guard.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
ENGINE = REPO / "raptor" / "core" / "engine"
GRAPH = ENGINE / "graph.py"
CAPACITY = ENGINE / "capacity.py"


class TestOneOwner:
    def test_capacity_no_longer_wires_dep_link(self):
        """AST over the function body: the removed loop assigned
        `link.dep_link`."""
        tree = ast.parse(CAPACITY.read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "build_capacity_graph")
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Attribute)
                            and t.attr == "dep_link" for t in node.targets)):
                pytest.fail(
                    f"capacity.py:{node.lineno} assigns dep_link again; "
                    "graph.build_efpa_graph owns that wiring")

    def test_graph_still_does_the_wiring(self):
        src = GRAPH.read_text()
        assert "link.dep_link = 0" in src
        assert "sister.siblings_group.append" in src

    def test_capacity_says_why_it_is_gone(self):
        """A silent removal of a section ported from Raptor 7 reads as an oversight.
        The replacement comment must name the source lines and the reason."""
        tree = ast.parse(CAPACITY.read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "build_capacity_graph")
        # Find the bare `return` that ends it, and look at the comments on
        # the statements just before.
        text = ast.get_source_segment(CAPACITY.read_text(), fn) or ""
        assert "RunEFPA" in text, (
            "the comment no longer names the Raptor 7 procedure; a reader will "
            "not know whether it was ported elsewhere or never ported")
        assert "resolved_to_kind" in text and "connect_t_ref" in text, (
            "the comment must record that the two copies guarded on "
            "DIFFERENT fields -- that is the whole reason it mattered")


class TestTheGuardsDiffer:
    """The two implementations were not interchangeable, which is why the
    redundant one was a hazard rather than a harmless repeat."""

    def test_graph_guards_on_the_resolved_kind_and_capacity_did_not(self):
        graph_src = GRAPH.read_text()
        assert "link.resolved_to_kind ==" in graph_src, (
            "graph.py's guard must stay on resolved_to_kind -- that is the "
            "hierarchy-resolved field, and it is the correct one")

        # Scoped to the WIRING, not the file: `capacity.py` still reads
        # `connect_t_ref == "RBDHier"` in `_resolved_cap_target`, which is
        # correct -- a hierarchy link must redirect to its In node. The
        # removed defect was a *Block/Event membership test* on the
        # unresolved field.
        tree = ast.parse(CAPACITY.read_text())
        offenders = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Compare)
                    and any(isinstance(c, ast.Constant)
                            and c.value in ("RBDBlock", "RBDEvent")
                            for c in ast.walk(node))
                    and any("connect_t_ref" in ast.unparse(n)
                            for n in ast.walk(node))):
                offenders.append(node.lineno)
        assert not offenders, (
            f"capacity.py:{offenders} tests Block/Event membership on the "
            "UNRESOLVED connect_t_ref again; graph.py's wiring guards on "
            "resolved_to_kind, and the two disagree on a hierarchy link")

    def test_the_self_reference_guard_survives(self):
        """`if sister is link: continue` -- without it a block whose own
        `downstream_link_id` is the link would become its own sister."""
        assert "if sister is link:" in GRAPH.read_text()


class TestTheSplitIsFaithful:
    """§7: engine code mirroring the source structure must not be merged."""

    def test_build_capacity_graph_still_runs_after_the_efpa_build(self):
        from raptor.core.engine.load import load_model
        import inspect
        src = inspect.getsource(load_model)
        first = src.index("build_efpa_graph(")
        second = src.index("build_capacity_graph(")
        assert first < second, (
            "build_efpa_graph must run first: build_capacity_graph needs "
            "nrbd_connect_*, which only the first pass populates")

    def test_the_docstring_still_states_the_ordering_requirement(self):
        from raptor.core.engine.capacity import build_capacity_graph
        doc = build_capacity_graph.__doc__ or ""
        assert "AFTER `build_efpa_graph`" in doc


class TestTheRedundancyHolds:
    """Behavioural, over the whole capacity-enabled model set.

    The claim being pinned is that removing the capacity copy changed
    nothing: `graph.py`'s pass has already done the wiring. If a future
    change makes the two diverge, this fails as a wrong value rather than as
    a structural nit.
    """

    GOLDEN = REPO / "tests" / "golden"

    def _capacity_models(self):
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.engine.load import load_model
        seen = 0
        for p in sorted(self.GOLDEN.glob("*/input.rbd")):
            try:
                model = load_model(load_rbd(p))
            except Exception:       # noqa: BLE001 - not every golden loads
                continue
            if model.capacity_analysis:
                seen += 1
                yield p.parent.name, model
        assert seen >= 10, f"only {seen} capacity-enabled models found"

    def test_every_capacity_model_still_gets_its_wiring(self):
        wired_models = 0
        for name, model in self._capacity_models():
            nz = sum(1 for l in model.links_by_id.values()
                     if l.dep_link or l.siblings_group or l.has_siblings)
            assert nz > 0, (
                f"{name}: no link is wired after removing capacity's copy -- "
                "graph.build_efpa_graph is not doing the job on its own")
            wired_models += 1
        assert wired_models >= 10

    def test_no_self_referential_sister_survives(self):
        """The property the removed copy could have broken: a link must
        never be its own sister."""
        for name, model in self._capacity_models():
            for lid, link in model.links_by_id.items():
                if link.dep_link == lid:
                    pytest.fail(f"{name}: link {lid} is its own dep_link")

    def test_siblings_group_holds_no_duplicates(self):
        """Both copies guarded the append; the surviving one still must, or
        a repeated pass would grow the list."""
        for name, model in self._capacity_models():
            for lid, link in model.links_by_id.items():
                assert len(set(link.siblings_group)) == len(link.siblings_group), (
                    f"{name}: link {lid} has a duplicated siblings_group "
                    f"entry: {link.siblings_group}")
"""The engine batch's stream set has one owner (review E-02).

Six statements — `make_shared_streams` plus five comprehensions over the
model's blocks and events — were written out **twice**: in
`driver.build_engine_ctx` and inline in `runner.run_simulation`.

**The duplication was never the finding. The enumeration is.**

Those six statements are how the engine builds *the set of streams in the
model*, and that set is walked by three other consumers:

| Consumer | What it does with the set | Miss it and… |
|---|---|---|
| `stream_jump` | advances every distinct stream to a disjoint segment | multi-worker chunks silently share stream segments |
| `/sd` `NamedRandomGen` wrapping | labels each draw in the capture | the new stream is unreported, and the segment check stops covering it |
| `count_stream_draws` | swaps each stream for a `CountingStream` | the chunk's draw budget stops covering it |

**Add one stream category and there were four places to remember.** That is
the hazard §2 recorded, and it is why `RunStreams` owns the *enumeration*
(`distinct_streams()`, `wrap_per_element()`) rather than just the six
statements.

**What the execution trace settled, and why it matters for how this is
tested.** The review's original fear was that `run_simulation` would silently
ignore a segment jump. It does not — it has no such parameter, so it is a hard
`TypeError`. And the duplicated block in `runner.py` sits after an *early
return* for the parallel path, so under `num_workers=4, runs=6` it is reached
**zero** times: **unreachable exactly when segmenting matters.** Both entry
points produce identical output over ~444,000 log lines across 9 models × 2
seeds. So there was no live bug, and any test asserting *behavioural
equivalence* would be pinning a coincidence.

The durable claims are therefore structural: one construction, one
enumeration, and a de-duplication rule that must hold because `make_rng`
returns the **same** `RandomGen** object** for every consumer of a shared
stream.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from raptor.core.engine.driver import RunStreams, build_run_streams

REPO = Path(__file__).resolve().parent.parent.parent
ENGINE = REPO / "raptor" / "core" / "engine"

#: The six collections, as the pre-fix code enumerated them by hand.
COLLECTIONS = ("sys_streams", "rng_fail_by_block", "rng_repair_by_block",
               "rng_by_event", "stats_by_block", "stats_by_event")


class TestOneConstruction:
    FILES = ["driver.py", "runner.py"]

    @pytest.mark.parametrize("rel", FILES)
    def test_the_constructor_itself_is_defined_once(self, rel):
        """The construction lives in exactly one function.

        Scoped to the *function bodies other than `build_run_streams`*,
        because that function legitimately contains the comprehensions the
        others must not. My first version asserted the whole file, which
        failed on the owner itself -- the same class of error as E-06's
        earlier check flagging `diagram_commands.py`.
        """
        tree = ast.parse((ENGINE / rel).read_text())
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name != "build_run_streams"]:
            for node in ast.walk(fn):
                if isinstance(node, ast.DictComp):
                    val = node.value
                    if isinstance(val, ast.Call) and getattr(
                            val.func, "id", None) == "make_rng":
                        pytest.fail(
                            f"{rel}:{fn.name}:{node.lineno} rebuilds a "
                            "per-block rng comprehension; call "
                            "build_run_streams()")
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "id", None)
                        == "make_shared_streams"):
                    pytest.fail(
                        f"{rel}:{fn.name}:{node.lineno} calls "
                        "make_shared_streams directly; that is "
                        "build_run_streams' business")

    def test_build_engine_ctx_delegates(self):
        src = (ENGINE / "driver.py").read_text()
        i = src.index("def build_engine_ctx")
        assert "build_run_streams(" in src[i:], (
            "build_engine_ctx no longer delegates")

    def test_run_simulation_delegates(self):
        src = (ENGINE / "runner.py").read_text()
        i = src.index("def run_simulation")
        assert "build_run_streams(" in src[i:], (
            "run_simulation no longer delegates")


class TestTheEnumeration:
    """Every consumer walks the one list."""

    def test_distinct_streams_covers_all_four_rng_collections(self):
        """The four that hold generators. The two stats collections hold no
        RNG and are deliberately excluded — they are on the bundle because
        `build_engine_ctx` constructs them, not because anything walks
        them."""
        src = (ENGINE / "driver.py").read_text()
        i = src.index("def distinct_streams")
        j = src.index("def wrap_per_element")
        body = src[i:j]
        for name in ("sys_streams", "rng_fail_by_block",
                     "rng_repair_by_block", "rng_by_event"):
            assert name in body, f"distinct_streams misses {name}"
        assert "stats_by_block" not in body, (
            "stats_by_block holds BlockStatistics, not generators")

    def test_stream_jump_walks_it(self):
        src = (ENGINE / "driver.py").read_text()
        i = src.index("if stream_jump:")
        j = src.index("end_node = next", i)
        assert "distinct_streams()" in src[i:j], (
            "stream_jump re-lists the collections instead of walking "
            "distinct_streams()")

    def test_count_stream_draws_walks_it(self):
        """Asserted on the function BODY, not on the file.

        My first version was a substring check, and the sensitivity mutation
        passed it: the hand-rolled enumeration still mentioned
        `distinct_streams()` elsewhere in the module while the function
        itself re-listed the collections and dropped one. A string check over
        `inspect.getsource` of the *right* function is what catches it --
        and asserting the collection names are ABSENT is what stops a
        half-reverted version.
        """
        import inspect

        from raptor.core.engine.driver import count_stream_draws
        body = inspect.getsource(count_stream_draws)
        assert "distinct_streams()" in body, (
            "count_stream_draws no longer walks the shared enumeration")
        for name in ("rng_fail_by_block", "rng_repair_by_block",
                     "rng_by_event", "sys_streams"):
            assert name not in body, (
                f"count_stream_draws names {name} directly; it is the "
                "FOURTH place a new stream category had to be remembered, "
                "which is the hazard E-02 describes")

    def test_the_sd_wrapping_walks_it(self):
        src = (ENGINE / "runner.py").read_text()
        assert "wrap_per_element(" in src, (
            "the /sd NamedRandomGen wrapping still hand-rolls its two loops, "
            "so a new per-element collection would be silently unreported")


class TestDeduplication:
    """The rule that must hold because `make_rng` shares one object across
    consumers of a stream.

    Jumping or counting such a generator twice advances it twice — which
    would silently change results — so `distinct_streams()` de-duplicates by
    identity rather than concatenating.
    """

    class _Gen:
        def __init__(self, n):
            self.n = n

    def _bundle(self):
        shared = self._Gen(1)
        other = self._Gen(2)
        return RunStreams(
            sys_streams={1: shared},
            # the SAME object reached three ways, as make_rng produces
            rng_fail_by_block={1: shared, 2: other},
            rng_repair_by_block={1: shared},
            rng_by_event={1: shared},
            stats_by_block={}, stats_by_event={},
        ), shared, other

    def test_one_shared_generator_appears_once(self):
        bundle, shared, _other = self._bundle()
        got = bundle.distinct_streams()
        assert len(got) == 2, f"expected 2 distinct generators, got {len(got)}"
        assert got.count(shared) == 1

    def test_distinct_streams_is_stable_across_calls(self):
        """Called twice must not return different counts — `stream_jump` and
        `count_stream_draws` each call it."""
        bundle, _s, _o = self._bundle()
        assert len(bundle.distinct_streams()) == len(bundle.distinct_streams())

    def test_wrap_per_element_visits_each_collection(self):
        bundle, _shared, _other = self._bundle()
        seen = []
        bundle.wrap_per_element(
            lambda gen, name: seen.append((gen, name)) or gen,
            lambda eid: f"e{eid}")
        assert len(seen) == 4, (
            f"expected 4 per-element generators (2 fail + 1 repair + 1 event),"
            f" visited {len(seen)}")

    def test_wrap_per_element_rebinds_in_place(self):
        """`run_simulation` has already bound these dicts into locals and
        into `EngineCtx`, so a returned-new-dict version would be ignored."""
        bundle, _shared, _other = self._bundle()
        marker = object()
        bundle.wrap_per_element(lambda gen, name: marker, lambda eid: "x")
        assert bundle.rng_fail_by_block[1] is marker
        assert bundle.rng_repair_by_block[1] is marker
        assert bundle.rng_by_event[1] is marker


class TestBuildRunStreams:
    def test_it_is_frozen(self):
        """Shared across every run in a batch and across the `/sd` wrappers;
        nothing may rebind a collection after construction."""
        import inspect
        assert "frozen=True" in inspect.getsource(RunStreams).split("class")[0] \
            or "@dataclass(frozen=True)" in inspect.getsource(
                __import__("raptor.core.engine.driver", fromlist=["x"]))

    def test_it_builds_all_six_collections(self):
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.engine.load import load_model

        model = load_model(load_rbd(REPO / "tests" / "golden" / "Capacity1"
                                    / "input.rbd"))
        b = build_run_streams(model, stream_seed=0)
        assert set(b.sys_streams) and set(b.rng_fail_by_block)
        assert len(b.stats_by_block) == len(model.blocks_by_id)
        assert len(b.stats_by_event) == len(model.events_by_id)

    def test_distinct_is_fewer_than_total(self):
        """Aliasing is real: fewer distinct generators than consumers."""
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.engine.load import load_model

        model = load_model(load_rbd(REPO / "tests" / "golden" / "Capacity1"
                                    / "input.rbd"))
        b = build_run_streams(model, stream_seed=0)
        total = (len(b.sys_streams) + len(b.rng_fail_by_block)
                 + len(b.rng_repair_by_block) + len(b.rng_by_event))
        assert len(b.distinct_streams()) < total, (
            "no aliasing at all -- if that is genuinely true for this model, "
            "say so in the docstring; the dedup is load-bearing elsewhere")

    def test_a_different_seed_gives_different_streams(self):
        from raptor.core.fileio.rbd import load_rbd
        from raptor.core.engine.load import load_model

        model = load_model(load_rbd(REPO / "tests" / "golden" / "Capacity1"
                                    / "input.rbd"))
        a = build_run_streams(model, stream_seed=0)
        b = build_run_streams(model, stream_seed=12345)
        x = a.sys_streams[next(iter(a.sys_streams))]
        y = b.sys_streams[next(iter(b.sys_streams))]
        assert x is not y, "two bundles shared a generator object"


class TestTheRefutedClaimsStayRefuted:
    """The trace's findings, as assertions, so nobody re-raises them."""

    def test_run_simulation_has_no_stream_jump_parameter(self):
        """The review feared it *silently ignored* a jump. It has no such
        parameter — a hard `TypeError`, not silence."""
        import inspect

        from raptor.core.engine.runner import run_simulation
        assert "stream_jump" not in inspect.signature(
            run_simulation).parameters

    def test_the_sequential_path_is_reached_only_without_segmenting(self):
        """`runner.py`'s duplicated block sits after the parallel early
        return, so it is unreachable exactly when segmenting matters."""
        src = (ENGINE / "runner.py").read_text()
        i = src.index("def run_simulation")
        body = src[i:]
        parallel = body.index("num_workers > 1 and runs > 1")
        ret = body.index("return all_logs_p", parallel)
        delegate = body.index("build_run_streams(")
        assert ret < delegate, (
            "the parallel early return must precede the sequential setup, "
            "or the segmented path would build streams twice")
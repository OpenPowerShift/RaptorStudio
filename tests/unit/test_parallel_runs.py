"""Multi-worker batch mode (independent stream segments)."""

from __future__ import annotations

import os
import pathlib

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd

REPO = pathlib.Path(__file__).resolve().parents[2]
GOLD = os.path.join(REPO, "tests", "golden")


def test_parallel_deterministic_and_independent():
    rbd = load_rbd(os.path.join(GOLD, "HierarchyExample", "input.rbd"))
    seq = run_simulation(rbd, num_runs=3)
    par_a = run_simulation(rbd, num_runs=3, num_workers=2)
    par_b = run_simulation(rbd, num_runs=3, num_workers=2)
    assert len(par_a) == 3 and len(par_b) == 3
    # worker seeding is deterministic -> identical repeat runs
    assert par_a == par_b
    # independent segments mean different draws from the sequential run
    assert par_a[0] != seq[0]


def test_the_answer_does_not_depend_on_how_many_workers():
    """The split into stream segments is fixed, so 2 and 3 workers (or a
    4-core and an 8-core machine) give the same numbers."""
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    two = run_simulation(rbd, num_runs=6, num_workers=2)
    three = run_simulation(rbd, num_runs=6, num_workers=3)
    assert two == three


def test_single_worker_matches_sequential():
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy",
                                "input.rbd"))
    seq = run_simulation(rbd, num_runs=2)
    one = run_simulation(rbd, num_runs=2, num_workers=1)
    assert seq == one


def test_reports_renumbered_after_merge():
    from raptor.core.engine.runner import run_simulation_with_reports
    sink = []
    run_simulation_with_reports(
        load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd")),
        num_runs=4, date="fixed", report_sink=sink, num_workers=2)
    assert [r.run_number for r in sink] == [1, 2, 3, 4]


def test_pool_grant_wake_does_not_steal_repair_spare_wait():
    """A pool-grant wake must not resume a repair-spare wait whose own spare
    is still queued. The scheduler carries the granted request's kind on
    the interrupt (`Scheduler.interrupt(..., token=...)`), so only the
    matching wait consumes it."""
    from raptor.core.engine.runner import _run_chunk_worker
    from raptor.core.fileio.rbds import render_rbds
    from raptor.core.rng import _M

    rbd = load_rbd(os.path.join(GOLD, "PhasingDemo", "input.rbd"))
    # The split the bug was found under (4 workers x 8 chunks, before the
    # chunk count stopped depending on the workers); the chunk worker is
    # called directly, so the geometry is this test's own.
    runs, n_chunks = 300, 32
    stride = max(_M - 1, 1) // max(n_chunks, 1)
    base, extra = divmod(runs, n_chunks)
    # Chunk to exercise; fall back to a valid index if the split geometry
    # changes.
    target = next((i for i in range(n_chunks)
                   if (i + 1) * stride == 1677721575), min(24, n_chunks - 1))
    chunk = base + (1 if target < extra else 0)
    res = _run_chunk_worker((render_rbds(rbd), chunk, (target + 1) * stride,
                             10.0, 10.0, False, 0, ""))
    assert res["done"] == chunk


def test_shared_stream_jumped_exactly_once():
    """Two blocks sharing a `SysStream` alias the same `RandomGen` object
    (`make_rng`, stream>200), so the parallel stream-jump must advance
    each distinct underlying stream once, not once per consumer."""
    from raptor.core.engine.driver import build_engine_ctx
    from raptor.core.rng import _A, _M

    rbd = load_rbd(os.path.join(GOLD, "SharedStreamTie", "input.rbd"))
    _, base = build_engine_ctx(rbd, num_runs=1, stream_jump=0)
    _, jumped = build_engine_ctx(rbd, num_runs=1, stream_jump=12345)

    b0 = list(base.rng_fail_by_block.values())
    j0 = list(jumped.rng_fail_by_block.values())
    assert b0[0] is b0[1], "blocks must alias the same shared RandomGen"
    assert j0[0] is j0[1]

    expected = (pow(_A, 12345, _M) * b0[0].stream._x) % _M
    assert j0[0].stream._x == expected


def test_counting_stream_draws_the_same_numbers_as_the_plain_one():
    from raptor.core.rng import CountingStream, RaptorStream
    plain, counting = RaptorStream(12345, antithetic=True), CountingStream(12345, antithetic=True)
    assert [plain.uniform() for _ in range(50)] == [counting.uniform() for _ in range(50)]
    assert counting.draws == 50


def test_a_chunk_reports_how_many_draws_it_used_and_stays_in_its_segment():
    from raptor.core.engine.runner import _run_chunk_worker
    from raptor.core.fileio.rbds import render_rbds
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    result = _run_chunk_worker((render_rbds(rbd), 2, 1000, 10.0, 720.0, False, 0, ""))
    assert 0 < result["max_draws"] < 1_000_000


def test_a_chunk_that_overruns_its_segment_is_refused():
    import pytest
    from raptor.core.engine.errors import StreamBudgetExceeded
    from raptor.core.engine.runner import check_chunk_draw_budget
    check_chunk_draw_budget(100, 100)
    with pytest.raises(StreamBudgetExceeded, match="not independent"):
        check_chunk_draw_budget(101, 100)


def test_count_stream_draws_swaps_each_shared_stream_once_and_leaves_wrapped_ones_alone() -> None:
    from types import SimpleNamespace

    from raptor.core.engine.driver import count_stream_draws
    from raptor.core.rng import CountingStream, RandomGen, RaptorStream

    shared = RandomGen(stream=RaptorStream(seed=7))
    own = RandomGen(stream=RaptorStream(seed=8))
    wrapped = SimpleNamespace(rng=own)          # not a `RandomGen`: what the /sd recorder returns
    ctx = SimpleNamespace(sys_streams={1: shared}, rng_fail_by_block={1: shared, 2: own},
                          rng_repair_by_block={3: wrapped}, rng_by_event={})
    counting = count_stream_draws(ctx)
    assert len(counting) == 2                    # `shared` reached three times, swapped once
    assert isinstance(shared.stream, CountingStream) and isinstance(own.stream, CountingStream)
    assert ctx.rng_repair_by_block[3] is wrapped


def test_progress_moves_between_chunk_merges():
    """Progress came only from merged chunks (in order), so a long run sat
    still; workers now count each finished run."""
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    seen = []
    run_simulation(rbd, num_runs=40, num_workers=2,
                   progress=lambda d, t: seen.append(d))
    assert seen and seen[-1] == 40
    assert seen == sorted(seen)
    # "More than one update" is the property under test: progress must move
    # before the final chunk merge. How MANY distinct values arrive is
    # scheduling-dependent (a loaded machine delivers coarser batches), so
    # asserting a count made this fail under `pytest -n 8` while passing
    # alone -- see the flake note in CONTRIBUTING.
    assert len(set(seen)) >= 2


def test_stop_keeps_the_runs_of_a_chunk_still_in_flight(monkeypatch):
    """Stop used to keep only chunks that had finished. A big batch has few,
    large chunks, so stopping after 70,000 counted runs kept none of them
    ("no runs complete"). Workers now hand back the runs they had done."""
    from raptor.core.engine import runner
    monkeypatch.setattr(runner, "_PARALLEL_CHUNKS", 2)
    rbd = load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))
    seen: list[int] = []
    sink: list = []
    total = 200_000
    logs = run_simulation(rbd, num_runs=total, num_workers=2, _report_sink=sink,
                          progress=lambda d, t: seen.append(d),
                          stop_check=lambda: bool(seen) and seen[-1] >= 20)
    assert 20 <= len(logs) < total              # runs of an unfinished chunk are kept
    assert len(sink) == len(logs)
    assert [row.run_number for row in sink] == list(range(1, len(sink) + 1))


# --- the steering hooks and sinks of both run paths -------------------------------------------
class _FlagStub:
    """Stands in for a `multiprocessing.Event` / `Value` inside this process."""

    def __init__(self, is_set: bool = False) -> None:
        self.set_ = is_set
        self.value = 0

    def is_set(self) -> bool:
        return self.set_

    def set(self) -> None:
        self.set_ = True

    def get_lock(self):
        import contextlib
        return contextlib.nullcontext()


def _two_block():
    return load_rbd(os.path.join(GOLD, "TwoBlockRedundancy", "input.rbd"))


def test_progress_raising_run_aborted_stops_a_parallel_batch(monkeypatch):
    from raptor.core.engine import runner
    from raptor.core.engine.errors import RunAborted
    monkeypatch.setattr(runner, "_PARALLEL_CHUNKS", 4)

    def stop_after_first(done, total):
        raise RunAborted()

    logs = run_simulation(_two_block(), num_runs=200_000, num_workers=2, progress=stop_after_first)
    assert len(logs) < 200_000


def test_pause_check_is_polled_while_a_parallel_batch_runs():
    polled = []
    run_simulation(_two_block(), num_runs=8, num_workers=2, pause_check=lambda: polled.append(1))
    assert polled


def test_a_parallel_batch_fills_every_output_sink():
    rbd = load_rbd(os.path.join(GOLD, "Capacity1", "input.rbd"))
    cap: list[str] = []
    ao: list[str] = []
    cost: list[str] = []
    meta: dict = {}
    run_simulation(rbd, num_runs=4, num_workers=2, _capacity_sink=cap, _ao_sink=ao, _cost_sink=cost,
                   metadata_sink=meta)
    assert cap and ao
    assert len(meta["negative_draws"]) == 4


def test_a_worker_told_to_stop_hands_back_only_the_runs_it_finished(monkeypatch):
    from raptor.core.engine import runner
    from raptor.core.fileio.rbds import render_rbds
    payload = (render_rbds(_two_block()), 5, 1000, 10.0, 720.0, False, 0, "")
    monkeypatch.setattr(runner, "_STOP_FLAG", _FlagStub(is_set=True))
    result = runner._run_chunk_worker(payload)
    assert result["done"] == 0 and result["logs"] == [] and result["negative_draws"] == []


def test_a_worker_counts_its_runs_and_raises_the_runaway_flag(monkeypatch):
    from raptor.core.engine import runner
    from raptor.core.fileio.rbds import render_rbds

    class Runaway(runner.SingleRunDriver):
        def finalize(self, *a, **k):
            out = super().finalize(*a, **k)
            self.hit_safety_bound = True
            return out

    runaway, counter = _FlagStub(), _FlagStub()
    monkeypatch.setattr(runner, "SingleRunDriver", Runaway)
    monkeypatch.setattr(runner, "_RUNAWAY_FLAG", runaway)
    monkeypatch.setattr(runner, "_RUNS_DONE", counter)
    result = runner._run_chunk_worker((render_rbds(_two_block()), 2, 1000, 10.0, 720.0, False, 0, ""))
    assert runaway.is_set() and result["runaway"] == 2 and counter.value == 2


def test_the_worker_start_up_records_its_flags():
    from raptor.core.engine import runner
    saved = (runner._RUNAWAY_FLAG, runner._RUNS_DONE, runner._STOP_FLAG)
    try:
        flag, counter, stop = _FlagStub(), _FlagStub(), _FlagStub()
        runner._init_chunk_worker(flag, counter, stop)
        assert (flag, counter, stop) == (runner._RUNAWAY_FLAG, runner._RUNS_DONE, runner._STOP_FLAG)
    finally:
        runner._RUNAWAY_FLAG, runner._RUNS_DONE, runner._STOP_FLAG = saved


def test_a_sequential_batch_honours_skip_stop_and_progress_abort():
    from raptor.core.engine.errors import RunAborted
    rbd = _two_block()

    skips = iter([True])
    kept = run_simulation(rbd, num_runs=3, skip_check=lambda: next(skips, False), stop_check=lambda: False)
    assert len(kept) == 2                       # the first run was abandoned, the rest went on

    stopped = run_simulation(rbd, num_runs=50, stop_check=lambda: True)
    assert len(stopped) < 50

    def abort(done, total):
        raise RunAborted()
    aborted = run_simulation(rbd, num_runs=50, progress=abort)
    assert len(aborted) == 1

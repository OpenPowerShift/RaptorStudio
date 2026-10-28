"""Smoke tests for `raptor.core.model`: every dataclass is well-formed and
constructible with defaults.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from raptor.core.engine.block_env import observing
from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.rbd import load_rbd
from raptor.core.model import (
    ActiveStatus,
    BlockRunState,
    BlockStatus,
    Link,
    LinkStatus,
    Phase,
    RBDBlock,
    RBDEvent,
    RBDHier,
    RBDNode,
    SparePool,
    SparingType,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden"


def test_all_dataclasses_construct_with_defaults() -> None:
    for cls in (RBDBlock, RBDEvent, RBDHier, RBDNode, Link, SparePool, Phase):
        instance = cls()
        assert instance is not None


def test_rbd_block_inherits_basic_fields() -> None:
    block = RBDBlock(name="unnamed", id=3, x_position=10.0, y_position=91.25)
    assert block.name == "unnamed"
    assert block.id == 3
    assert block.active_status == ActiveStatus.ACTIVE  # inherited default


def test_rbd_block_matches_uniformprobe_fixture_config() -> None:
    # Mirrors the actual block config in tests/golden/UniformProbe/input.rbd:
    # FDist=10 (Uniform Real), FStream=1, FParam1=0, FParam2=1;
    # RDist=10, RStream=2, RParam1=0, RParam2=1.
    block = RBDBlock(
        name="unnamed",
        fail_distro=10,
        fail_stream=1,
        fail_values=[0.0, 1.0],
        repair_distro=10,
        repair_stream=2,
        repair_values=[0.0, 1.0],
        sparing_type=SparingType.INFINITE,
    )
    assert block.fail_values == [0.0, 1.0]
    assert block.sparing_type is SparingType.INFINITE


def test_link_does_not_inherit_rbd_basic() -> None:
    # `Link` is intentionally not a subclass of `RBDBasic`; this test pins
    # that design choice.
    link = Link()
    assert not hasattr(link, "name")
    assert hasattr(link, "status")
    assert link.status == LinkStatus.IDLE


def test_block_status_enum_values_match_source() -> None:
    # BlockStatusType=(Running,Repairing,Done,Idle,Standby,Workspace,PM,PMhold,RepHold)
    assert {s.value for s in BlockStatus} == {
        "Running", "Repairing", "Done", "Idle", "Standby",
        "Workspace", "PM", "PMhold", "RepHold",
    }


def test_block_runtime_fields_are_not_config_fields() -> None:
    # A field is either loaded from the `.rbd` file
    # (`RBDBlock`) or changed by a run (`BlockRunState`), never both.
    config = {f.name for f in dataclasses.fields(RBDBlock)}
    runtime = {f.name for f in dataclasses.fields(BlockRunState)}
    assert not config & runtime
    assert RBDBlock().run_state == BlockRunState()
    assert RBDBlock().run_state is not RBDBlock().run_state


def test_every_run_starts_with_a_fresh_block_runtime() -> None:
    # The driver's per-run reset is a new `BlockRunState`, so no flag a run
    # leaves behind (a block parked mid fail-wait, a cold path marked done)
    # reaches the next run.
    seen: dict[int, list[BlockRunState]] = {}

    def _note(block) -> None:
        runtimes = seen.setdefault(block.id, [])
        if not any(rt is block.run_state for rt in runtimes):
            runtimes.append(block.run_state)  # kept alive, so identities stay distinct

    class _Recorder:
        def on_wake(self, block, site, wake) -> None:
            _note(block)

        def on_transition(self, block, old, new, comment) -> None:
            _note(block)

    rbd = load_rbd(str(GOLDEN / "CS_SwitchFail" / "input.rbd"))
    with observing(_Recorder()):
        run_simulation(rbd, num_runs=2, collect_logs=False)
    assert seen
    assert all(len(runtimes) == 2 for runtimes in seen.values())

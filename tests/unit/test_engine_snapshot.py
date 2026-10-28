"""Engine snapshot gate: every model's output is byte-identical to the last
recorded run of this engine (`tests/golden/SNAPSHOT.toml`).

The real-capture tests only compare fixtures against real Raptor, so a change
to a model that does not yet match real goes unnoticed there. This gate
notices any change at all: event logs, both reports, every random draw,
capacity and interim-cost output, for every `.rbd` under `tests/golden/` and
`tests/fixtures/`.

A `refactor:` commit must leave this green without regenerating. A `fix:`
commit that changes behaviour on purpose regenerates the file with
`python tools/engine_snapshot.py --write` and names the moved models in its
message (CONTRIBUTING.md). `tools/engine_snapshot.py --dump` writes the
artifact text so the change can be diffed against the previous commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "tools"))
import engine_snapshot as snap  # noqa: E402

_RECORDED = snap.read_snapshot()


def test_snapshot_lists_every_model() -> None:
    on_disk = set(snap.model_keys())
    assert on_disk == set(_RECORDED), (
        "models added or removed since SNAPSHOT.toml was written: "
        f"new={sorted(on_disk - set(_RECORDED))} gone={sorted(set(_RECORDED) - on_disk)}; "
        "run `python tools/engine_snapshot.py --write <key>`")


@pytest.mark.parametrize("key", snap.model_keys())
def test_engine_output_unchanged(key: str) -> None:
    got = snap.snapshot_entry(key)
    want = _RECORDED.get(key)
    if got != want:
        moved = sorted(n for n in set(got) | set(want or {}) if got.get(n) != (want or {}).get(n))
        pytest.fail(
            f"{key}: {', '.join(moved)} changed ({want} -> {got}). If intended, regenerate with "
            f"`python tools/engine_snapshot.py --write {key}` and name it in the commit; "
            f"to see the change, `--dump` at the previous commit and here, then diff.")

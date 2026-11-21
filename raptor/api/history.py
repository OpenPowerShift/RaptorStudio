"""Public API for version history (see `raptor.core.history`)."""
from __future__ import annotations

from pathlib import Path

from raptor.core.history import HistoryStore, Snapshot, default_store


def store() -> HistoryStore:
    return default_store()


def record(path: str | Path, *, reason: str = "save", label: str = "",
           data: bytes | None = None) -> Snapshot | None:
    return default_store().record(path, reason=reason, label=label, data=data)


def snapshots(path: str | Path) -> list[Snapshot]:
    return default_store().snapshots(path)


def restore(path: str | Path, snapshot_id: str,
            target: str | Path | None = None) -> Path:
    store_ = default_store()
    found = store_.snapshot(path, snapshot_id)
    if found is None:
        raise FileNotFoundError(f"snapshot {snapshot_id!r} not found")
    return store_.restore(found, target)


def discard(path: str | Path, snapshot_id: str) -> bool:
    return default_store().discard(path, snapshot_id)

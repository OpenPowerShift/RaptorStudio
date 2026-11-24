"""Public API for version history (see `raptor.core.history`)."""
from __future__ import annotations

from pathlib import Path

from raptor.core.history import HistoryStore, Snapshot, default_store


def store() -> HistoryStore:
    return default_store()


def record(path: str | Path, *, reason: str = "save", label: str = "",
           data: bytes | None = None,
           description: str = "") -> Snapshot | None:
    return default_store().record(path, reason=reason, label=label, data=data,
                                  description=description)


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


def set_label(path: str | Path, snapshot_id: str, label: str) -> bool:
    return default_store().set_label(path, snapshot_id, label)



def set_description(path: str | Path, snapshot_id: str, text: str) -> bool:
    """Set or clear a version's Markdown description (metadata only)."""
    return default_store().set_description(path, snapshot_id, text)

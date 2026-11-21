"""Version history for model files.

Every successful save appends an immutable snapshot of the file. The
snapshots live in a private per-user folder, not beside the model, so a
project folder stays clean and a read-only network share still gets
history. The index and each snapshot are written atomically; a crash
mid-save leaves either the old version or the new one, never a truncated
entry.

The store is deliberately file-based and dependency-free: the GUI, the
command line and any future server can share it without importing Qt.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raptor.core.state_dir import state_dir

#: Keep this many versions per file...
DEFAULT_KEEP = 30
#: ... and never let one file's history exceed this many bytes.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
INDEX_VERSION = 1


class HistoryError(Exception):
    """A snapshot cannot be read or written."""


@dataclass(frozen=True)
class Snapshot:
    id: str
    file: str
    created: str
    reason: str
    label: str
    bytes: int
    sha256: str
    path: Path

    def time_text(self) -> str:
        try:
            stamp = datetime.fromisoformat(self.created)
        except ValueError:
            return self.created
        return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def size_text(self) -> str:
        if self.bytes < 1024:
            return f"{self.bytes} B"
        if self.bytes < 1024 * 1024:
            return f"{self.bytes / 1024:.1f} KB"
        return f"{self.bytes / (1024 * 1024):.1f} MB"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class HistoryStore:
    """One user's version history for model files."""

    def __init__(self, root: str | Path | None = None, *,
                 keep: int = DEFAULT_KEEP,
                 max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = Path(root) if root is not None else state_dir("history")
        self.keep = max(1, int(keep))
        self.max_bytes = max(0, int(max_bytes))
        self._lock = threading.Lock()

    # -- paths ----------------------------------------------------------
    @staticmethod
    def _key(file: str | Path) -> str:
        text = os.path.normcase(str(Path(file).expanduser().resolve()))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _folder(self, file: str | Path) -> Path:
        return self.root / self._key(file)

    def _index_path(self, file: str | Path) -> Path:
        return self._folder(file) / "index.json"

    # -- index ----------------------------------------------------------
    def _read_index(self, file: str | Path) -> dict[str, Any]:
        path = self._index_path(file)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict) or not isinstance(raw.get("snapshots"), list):
            raw = {"version": INDEX_VERSION, "file": str(file), "snapshots": []}
        return raw

    def _write_index(self, file: str | Path, doc: dict[str, Any]) -> None:
        _atomic_write(self._index_path(file),
                      (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode())

    def snapshots(self, file: str | Path) -> list[Snapshot]:
        """Newest first."""
        folder = self._folder(file)
        doc = self._read_index(file)
        out: list[Snapshot] = []
        for entry in doc.get("snapshots", []):
            if not isinstance(entry, dict):
                continue
            sid = str(entry.get("id") or "")
            name = str(entry.get("stored") or "")
            if not sid or not name:
                continue
            path = folder / name
            if not path.is_file():
                continue
            try:
                size = int(entry.get("bytes", path.stat().st_size))
            except (TypeError, ValueError, OSError):
                size = path.stat().st_size
            out.append(Snapshot(
                id=sid, file=str(entry.get("file") or file),
                created=str(entry.get("created") or ""),
                reason=str(entry.get("reason") or ""),
                label=str(entry.get("label") or ""),
                bytes=size, sha256=str(entry.get("sha256") or ""),
                path=path))
        out.sort(key=lambda s: (s.created, s.id), reverse=True)
        return out

    # -- writing --------------------------------------------------------
    def record(self, file: str | Path, *, reason: str = "save",
               label: str = "", data: bytes | None = None) -> Snapshot | None:
        """Snapshot the file as it is now. Returns None when unchanged.

        `data` lets a caller snapshot bytes it already holds (a save that has
        not hit disk yet); otherwise the file is read.
        """
        path = Path(file)
        try:
            payload = data if data is not None else path.read_bytes()
        except OSError as exc:
            raise HistoryError(f"cannot read {path}: {exc}") from exc
        digest = hashlib.sha256(payload).hexdigest()
        with self._lock:
            existing = self.snapshots(path)
            if existing and existing[0].sha256 == digest:
                return None
            now = datetime.now(UTC)
            # The id is also the filename stem; two saves in one second get
            # a short content suffix so they cannot overwrite each other.
            sid = now.strftime("%Y%m%dT%H%M%S%f") + "-" + digest[:6]
            suffix = path.suffix or ".rbds"
            stored = sid + suffix
            folder = self._folder(path)
            target = folder / stored
            _atomic_write(target, payload)
            doc = self._read_index(path)
            entries = [e for e in doc.get("snapshots", [])
                       if isinstance(e, dict)]
            entries.append({
                "id": sid, "file": str(path), "created": now.isoformat(
                    timespec="seconds"),
                "reason": str(reason or "save"), "label": str(label or ""),
                "bytes": len(payload), "sha256": digest, "stored": stored,
            })
            doc = self._prune(path, entries)
            self._write_index(path, doc)
            return Snapshot(
                id=sid, file=str(path),
                created=now.isoformat(timespec="seconds"),
                reason=str(reason or "save"), label=str(label or ""),
                bytes=len(payload), sha256=digest, path=target)

    def _prune(self, file: str | Path, entries: list[dict[str, Any]]
               ) -> dict[str, Any]:
        entries = sorted(
            entries, key=lambda e: (str(e.get("created") or ""),
                                    str(e.get("id") or "")), reverse=True)
        kept: list[dict[str, Any]] = []
        total = 0
        for entry in entries:
            name = str(entry.get("stored") or "")
            size = int(entry.get("bytes") or 0)
            if len(kept) >= self.keep or (kept and total + size > self.max_bytes):
                if name:
                    try:
                        (self._folder(file) / name).unlink()
                    except OSError:
                        pass
                continue
            kept.append(entry)
            total += size
        return {"version": INDEX_VERSION, "file": str(file),
                "snapshots": kept}

    # -- reading / restoring -------------------------------------------
    def snapshot(self, file: str | Path, snapshot_id: str) -> Snapshot | None:
        return next((s for s in self.snapshots(file)
                     if s.id == snapshot_id), None)

    def read(self, snapshot: Snapshot) -> bytes:
        try:
            return snapshot.path.read_bytes()
        except OSError as exc:
            raise HistoryError(
                f"snapshot {snapshot.id} is no longer available") from exc

    def restore(self, snapshot: Snapshot, target: str | Path | None = None
                ) -> Path:
        """Write the snapshot over `target` (default: its original file)."""
        destination = Path(target) if target is not None else Path(snapshot.file)
        _atomic_write(destination, self.read(snapshot))
        return destination

    def discard(self, file: str | Path, snapshot_id: str) -> bool:
        with self._lock:
            doc = self._read_index(file)
            kept = []
            removed = None
            for entry in doc.get("snapshots", []):
                if isinstance(entry, dict) and entry.get("id") == snapshot_id:
                    removed = entry
                else:
                    kept.append(entry)
            if removed is None:
                return False
            name = str(removed.get("stored") or "")
            if name:
                try:
                    (self._folder(file) / name).unlink()
                except OSError:
                    pass
            doc["snapshots"] = kept
            self._write_index(file, doc)
            return True


def default_store() -> HistoryStore:
    return HistoryStore()

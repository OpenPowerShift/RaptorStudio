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
import stat
import tempfile
import threading
from contextlib import contextmanager
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
#: Longest label accepted; labels are metadata, not part of the snapshot.
MAX_LABEL = 200
#: Longest Markdown description accepted (characters). Like the label it is
#: metadata in the index, never part of the snapshot bytes or their hash.
MAX_DESCRIPTION = 20_000

_umask_lock = threading.Lock()


def file_digest(path: str | Path) -> str:
    """SHA-256 of *path*, or "" when it cannot be read.

    The GUI and CLI use this to mark the version that matches the file as
    it is on disk right now.
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def clean_label(label: str | None) -> str:
    """One line, bounded length: labels are shown in lists and messages."""
    text = " ".join(str(label or "").split())
    return text[:MAX_LABEL]


def clean_description(text: str | None) -> str:
    """Multi-line Markdown, bounded: newlines kept, control characters not.

    Line endings are normalised to LF and trailing blank space trimmed; the
    text is stored and shown as data, never interpreted.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = "".join(ch for ch in raw
                  if ch in "\n\t" or (ch >= " " and ch != "\x7f"))
    return raw.strip()[:MAX_DESCRIPTION].rstrip()


@contextmanager
def _file_lock(path: Path):
    """Best-effort inter-process lock beside the index."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass


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
    #: Optional Markdown note about this version (metadata only).
    description: str = ""

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


def _new_file_mode() -> int:
    """The mode a normal `open(..., "w")` would give a new file."""
    with _umask_lock:
        mask = os.umask(0)
        os.umask(mask)
    return 0o666 & ~mask


def _atomic_write(path: Path, data: bytes) -> None:
    # Write through a symlink rather than replacing the link itself.
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = path.stat()
    except OSError:
        previous = None
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if previous is not None:
            # Preserve the owner's read/write/execute bits, including a
            # group-shared model's group permissions, but never carry
            # setuid/setgid/sticky bits onto the replacement.
            try:
                os.chmod(tmp, stat.S_IMODE(previous.st_mode) & 0o777)
            except OSError:
                pass
            try:
                os.chown(tmp, previous.st_uid, previous.st_gid)
            except (AttributeError, OSError):
                pass
        else:
            try:
                os.chmod(tmp, _new_file_mode())
            except OSError:
                pass
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
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
        except FileNotFoundError:
            raw = {"version": INDEX_VERSION, "file": str(file), "snapshots": []}
        except (OSError, ValueError):
            # Keep the damaged index for diagnosis; rebuild what the file
            # names can tell us so snapshots are not lost.
            try:
                quarantine = path.with_name(
                    f"{path.name}.corrupt-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}")
                os.replace(path, quarantine)
            except OSError:
                pass
            raw = self._rebuild_index(file)
        if not isinstance(raw, dict) or not isinstance(raw.get("snapshots"), list):
            raw = self._rebuild_index(file)
        raw["snapshots"] = [entry for entry in raw.get("snapshots", [])
                            if self._safe_stored(entry)]
        return raw

    @staticmethod
    def _safe_stored(entry: Any) -> bool:
        """A snapshot filename must stay inside its own history folder."""
        if not isinstance(entry, dict):
            return False
        name = str(entry.get("stored") or "")
        return (bool(name) and Path(name).name == name
                and name not in (".", "..")
                and "/" not in name and "\\" not in name and "\0" not in name)

    def _rebuild_index(self, file: str | Path) -> dict[str, Any]:
        entries = []
        folder = self._folder(file)
        for path in sorted(folder.glob("*.rb*")):
            if path.suffix not in (".rbds", ".rbd"):
                continue
            sid = path.stem
            stamp = sid.split("-", 1)[0]
            try:
                created = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T" \
                          f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
            except Exception:  # noqa: BLE001
                created = ""
            try:
                size = path.stat().st_size
            except OSError:
                continue
            entries.append({"id": sid, "file": str(file), "created": created,
                            "reason": "recovered", "label": "",
                            "description": "",
                            "bytes": size, "sha256": file_digest(path),
                            "stored": path.name})
        return {"version": INDEX_VERSION, "file": str(file),
                "snapshots": entries}

    def _write_index(self, file: str | Path, doc: dict[str, Any]) -> None:
        _atomic_write(self._index_path(file),
                      (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode())

    def snapshots(self, file: str | Path) -> list[Snapshot]:
        """Newest first."""
        folder = self._folder(file)
        doc = self._read_index(file)
        if not doc.get("snapshots") and any(folder.glob("*.rb*")):
            doc = self._rebuild_index(file)
            try:
                self._write_index(file, doc)     # persist the recovery
            except OSError:
                pass
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
                path=path,
                description=clean_description(entry.get("description"))))
        out.sort(key=lambda s: (s.created, s.id), reverse=True)
        return out

    # -- writing --------------------------------------------------------
    def record(self, file: str | Path, *, reason: str = "save",
               label: str = "", data: bytes | None = None,
               description: str = "") -> Snapshot | None:
        """Snapshot the file as it is now. Returns None when unchanged.

        `data` lets a caller snapshot bytes it already holds (a save that has
        not hit disk yet); otherwise the file is read.

        A label on identical bytes re-labels the newest version instead of
        creating a duplicate snapshot, so `--snapshot --label` on an
        unchanged file still does the useful thing. A description on
        identical bytes is likewise set on the newest version (a blank one
        leaves the existing description alone).
        """
        path = Path(file)
        try:
            payload = data if data is not None else path.read_bytes()
        except OSError as exc:
            raise HistoryError(f"cannot read {path}: {exc}") from exc
        digest = hashlib.sha256(payload).hexdigest()
        label = clean_label(label)
        description = clean_description(description)
        with self._lock, _file_lock(self._folder(path) / "index.lock"):
            existing = self.snapshots(path)
            if existing and existing[0].sha256 == digest:
                latest = existing[0]
                new_label = label if label and latest.label != label \
                    else latest.label
                new_desc = description if description \
                    and latest.description != description \
                    else latest.description
                if (new_label, new_desc) != (latest.label, latest.description):
                    doc = self._read_index(path)
                    for entry in doc.get("snapshots", []):
                        if isinstance(entry, dict) and \
                                entry.get("id") == latest.id:
                            entry["label"] = new_label
                            if new_desc:
                                entry["description"] = new_desc
                    self._write_index(path, doc)
                    return Snapshot(
                        id=latest.id, file=latest.file, created=latest.created,
                        reason=latest.reason, label=new_label,
                        bytes=latest.bytes, sha256=latest.sha256,
                        path=latest.path, description=new_desc)
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
                "reason": str(reason or "save"), "label": label,
                "bytes": len(payload), "sha256": digest, "stored": stored,
                **({"description": description} if description else {}),
            })
            doc, removed = self._prune(path, entries)
            # The index is the truth: commit it first, then delete the
            # files it no longer names. A crash in between leaves an
            # unreferenced snapshot (recoverable) rather than an index
            # entry whose file is gone.
            self._write_index(path, doc)
            self._unlink(path, removed)
            return Snapshot(
                id=sid, file=str(path),
                created=now.isoformat(timespec="seconds"),
                reason=str(reason or "save"), label=label,
                bytes=len(payload), sha256=digest, path=target,
                description=description)

    def _entry_size(self, folder: Path, entry: dict[str, Any]) -> int:
        try:
            size = int(entry.get("bytes") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > 0:
            return size
        try:
            return (folder / str(entry.get("stored") or "")).stat().st_size
        except OSError:
            return 0

    def _prune(self, file: str | Path, entries: list[dict[str, Any]]
               ) -> tuple[dict[str, Any], list[str]]:
        """Keep the newest versions within both limits.

        A labelled or described version is pinned against `keep`: it is not
        counted, so naming or describing one keeps it until the user deletes it
        or clears both.
        The byte cap is still a ceiling for the whole store (pinned files
        included), checked between versions; the newest version is always
        kept even when it alone is larger than the cap. Returns the new
        index and the stored names to delete after it is written.
        """
        folder = self._folder(file)
        entries = sorted(
            entries, key=lambda e: (str(e.get("created") or ""),
                                    str(e.get("id") or "")), reverse=True)
        kept: list[dict[str, Any]] = []
        removed: list[str] = []
        total = 0
        unpinned_kept = 0
        for entry in entries:
            name = str(entry.get("stored") or "")
            size = self._entry_size(folder, entry)
            pinned = bool(clean_label(entry.get("label"))
                          or clean_description(entry.get("description")))
            too_many = not pinned and unpinned_kept >= self.keep
            # A label pins against the count, not against the disk: the
            # byte cap is a ceiling for the store as a whole.
            too_big = (total + size > self.max_bytes
                       and (pinned or unpinned_kept))
            if too_many or too_big:
                if name:
                    removed.append(name)
                continue
            kept.append(entry)
            total += size
            if not pinned:
                unpinned_kept += 1
        return {"version": INDEX_VERSION, "file": str(file),
                "snapshots": kept}, removed

    def _unlink(self, file: str | Path, names: list[str]) -> None:
        folder = self._folder(file)
        for name in names:
            try:
                (folder / name).unlink()
            except OSError:
                pass

    def set_label(self, file: str | Path, snapshot_id: str, label: str) -> bool:
        """Set or clear the label of one snapshot; content is unchanged."""
        text = clean_label(label)
        with self._lock, _file_lock(self._folder(file) / "index.lock"):
            doc = self._read_index(file)
            for entry in doc.get("snapshots", []):
                if isinstance(entry, dict) and entry.get("id") == snapshot_id:
                    entry["label"] = text
                    self._write_index(file, doc)
                    return True
            return False

    def set_description(self, file: str | Path, snapshot_id: str,
                        text: str) -> bool:
        """Set or clear the Markdown description of one snapshot.

        Metadata only: the snapshot bytes and their checksum are untouched.
        """
        clean = clean_description(text)
        with self._lock, _file_lock(self._folder(file) / "index.lock"):
            doc = self._read_index(file)
            for entry in doc.get("snapshots", []):
                if isinstance(entry, dict) and entry.get("id") == snapshot_id:
                    if clean:
                        entry["description"] = clean
                    else:
                        entry.pop("description", None)
                    self._write_index(file, doc)
                    return True
            return False

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
        payload = self.read(snapshot)
        if snapshot.sha256 and hashlib.sha256(payload).hexdigest() \
                != snapshot.sha256:
            raise HistoryError(
                f"snapshot {snapshot.id} is damaged (checksum mismatch); "
                "the target was not changed")
        destination = Path(target) if target is not None else Path(snapshot.file)
        _atomic_write(destination, payload)
        return destination

    def discard(self, file: str | Path, snapshot_id: str) -> bool:
        with self._lock, _file_lock(self._folder(file) / "index.lock"):
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
            doc["snapshots"] = kept
            self._write_index(file, doc)
            name = str(removed.get("stored") or "")
            if name:
                try:
                    (self._folder(file) / name).unlink()
                except OSError:
                    pass
            return True


def default_store() -> HistoryStore:
    return HistoryStore()

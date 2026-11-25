"""Version history for model files.

A version is an explicit checkpoint: the user saves one ("Save version
now", `raptor history --snapshot`, the MCP `snapshot_version` tool). An
ordinary save records nothing. The one automatic copy is the file as it was
just before a restore overwrites it. Versions are immutable snapshots in a
private per-user folder, not beside the model, so a project folder stays
clean and a read-only network share still gets history. Identical bytes are
stored once (the stored name is the content hash), however many entries
name them. The index and each snapshot are written atomically; a crash
mid-save leaves either the old version or the new one, never a truncated
entry.

The store is deliberately file-based and dependency-free: the GUI, the
command line and any future server can share it without importing Qt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raptor.core.state_dir import state_dir

_fcntl: Any
try:                                    # POSIX
    import fcntl as _fcntl
except ImportError:                     # Windows
    _fcntl = None

#: Automatic copies only (see AUTOMATIC_REASONS): keep this many per file...
DEFAULT_KEEP = 30
#: ... and let them use up to this many bytes. Explicit versions are never
#: deleted automatically; this is also the size above which the dialog warns
#: that a model's history is large.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
#: Reasons that mark a version as taken by the program, not the user: the
#: copy before a restore, and the entries older releases wrote on every save
#: ("save"), after a restore ("restored") and on open ("opened"). Only these
#: are pruned by the limits; a label or description makes any of them the
#: user's, and it is then kept.
AUTOMATIC_REASONS = frozenset({"before restore", "save", "restored",
                               "opened"})
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


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


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


#: Windows (no fcntl) falls back to an exclusive lock file. One older than
#: this many seconds is taken to be left by a crashed process and is broken.
LOCK_STALE_SECONDS = 60.0
#: How long to wait for another process's lock before giving up on it.
LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL = 0.05
#: A temp file left by an interrupted write is swept after this long.
ORPHAN_PART_SECONDS = 3600.0


@contextmanager
def _file_lock(path: Path):
    """Inter-process lock beside the index.

    POSIX uses flock. Where fcntl is missing (Windows) an O_CREAT|O_EXCL
    lock file is used instead, with a stale-lock timeout so a crashed
    process cannot wedge the history for good.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if _fcntl is not None:
        with open(path, "a+", encoding="utf-8") as handle:
            try:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            except OSError:
                pass
            try:
                yield
            finally:
                try:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
                except OSError:
                    pass
        return
    marker = path.with_name(path.name + ".excl")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except (FileExistsError, PermissionError):
            try:
                age = time.time() - marker.stat().st_mtime
            except OSError:
                continue                # vanished: try to take it again
            if age > LOCK_STALE_SECONDS or time.monotonic() > deadline:
                _discard(marker)        # stale (or wedged): break it
                continue
            time.sleep(_LOCK_POLL)
    try:
        yield
    finally:
        _discard(marker)


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

    def time_text(self, seconds: bool = True) -> str:
        """Local date and time; ``seconds=False`` stops at the minute."""
        try:
            stamp = datetime.fromisoformat(self.created)
        except ValueError:
            return self.created
        return stamp.astimezone().strftime(
            "%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")

    def size_text(self) -> str:
        return format_bytes(self.bytes)

    def is_automatic(self) -> bool:
        """Taken by the program and not named by the user: prunable."""
        return _is_automatic({"reason": self.reason, "label": self.label,
                              "description": self.description})


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


def _stage_copy(path: Path, folder: Path, progress: Any = None
                ) -> tuple[Path, str, int]:
    """Stream *path* into a temp file in *folder*, hashing as it goes.

    One pass, constant memory: returns ``(temp path, sha256, size)``. The
    caller renames the temp file to its content-addressed name (an atomic
    replace within one folder) or deletes it. ``progress(done, total)``
    follows the bytes copied.
    """
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    done = 0
    fd, tmp = tempfile.mkstemp(prefix="stage.", suffix=".part",
                               dir=str(folder))
    try:
        with open(path, "rb") as src, os.fdopen(fd, "wb") as out:
            total = os.fstat(src.fileno()).st_size
            while chunk := src.read(1024 * 1024):
                digest.update(chunk)
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, max(total, done))
            out.flush()
            os.fsync(out.fileno())
        try:
            os.chmod(tmp, _new_file_mode())
        except OSError:
            pass
    except OSError as exc:
        _discard(Path(tmp))
        raise HistoryError(f"cannot read {path}: {exc}") from exc
    except BaseException:
        _discard(Path(tmp))
        raise
    return Path(tmp), digest.hexdigest(), done


def _discard(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _is_automatic(entry: dict[str, Any]) -> bool:
    return (str(entry.get("reason") or "") in AUTOMATIC_REASONS
            and not clean_label(entry.get("label"))
            and not clean_description(entry.get("description")))


class HistoryStore:
    """One user's version history for model files."""

    def __init__(self, root: str | Path | None = None, *,
                 keep: int = DEFAULT_KEEP,
                 max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = Path(root) if root is not None else state_dir("history")
        self.keep = max(1, int(keep))
        self.max_bytes = max(0, int(max_bytes))
        self._lock = threading.Lock()
        self._sweep_orphans()

    def _sweep_orphans(self) -> None:
        """Remove temp files an interrupted write left behind (> 1 hour)."""
        cutoff = time.time() - ORPHAN_PART_SECONDS
        try:
            candidates = list(self.root.glob("*/*.part"))
        except OSError:
            return
        for part in candidates:
            try:
                if part.stat().st_mtime < cutoff:
                    part.unlink()
            except OSError:
                pass

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
        raw: Any
        for attempt in range(4):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                break
            except FileNotFoundError:
                raw = {"version": INDEX_VERSION, "file": str(file),
                       "snapshots": []}
                break
            except ValueError:
                raw = None              # unparseable: quarantine below
                break
            except OSError as exc:
                # A transient failure (a share hiccup, a virus scanner
                # holding the file) is not damage: never rebuild over it.
                if attempt == 3:
                    raise HistoryError(
                        f"cannot read the version history index "
                        f"({path}): {exc}") from exc
                time.sleep(0.05 * (attempt + 1))
        if not isinstance(raw, dict) or not isinstance(raw.get("snapshots"),
                                                       list):
            # Keep the damaged index for diagnosis; rebuild what the file
            # names can tell us so snapshots are not lost.
            try:
                quarantine = path.with_name(
                    f"{path.name}.corrupt-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}")
                os.replace(path, quarantine)
            except OSError:
                pass
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

    def _rebuild_index(self, file: str | Path,
                       deleted: list[str] | None = None) -> dict[str, Any]:
        entries = []
        folder = self._folder(file)
        skip = set(deleted or [])
        for path in sorted(folder.glob("*.rb*")):
            if path.suffix not in (".rbds", ".rbd", ".rbdz"):
                continue
            if path.name in skip:
                continue            # deleted by the user; unlink failed
            sid = path.stem
            try:
                size = path.stat().st_size
            except OSError:
                continue
            stamp = sid.split("-", 1)[0]
            if re.fullmatch(r"\d{8}T\d{6,}", stamp):
                created = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T" \
                          f"{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
            else:
                # Content-addressed name: the file's own time is all there is.
                created = datetime.fromtimestamp(
                    path.stat().st_mtime, UTC).isoformat(timespec="seconds")
            entries.append({"id": sid, "file": str(file), "created": created,
                            "reason": "recovered", "label": "",
                            "description": "",
                            "bytes": size, "sha256": file_digest(path),
                            "stored": path.name})
        doc: dict[str, Any] = {"version": INDEX_VERSION, "file": str(file),
                               "snapshots": entries}
        if skip:
            doc["deleted"] = sorted(skip)
        return doc

    def _write_index(self, file: str | Path, doc: dict[str, Any]) -> None:
        _atomic_write(self._index_path(file),
                      (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode())

    def snapshots(self, file: str | Path) -> list[Snapshot]:
        """Newest first."""
        folder = self._folder(file)
        doc = self._read_index(file)
        if not doc.get("snapshots") and any(folder.glob("*.rb*")):
            doc = self._rebuild_index(file, self._pending_deletions(doc))
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
               description: str = "",
               progress: Any = None) -> Snapshot | None:
        """Checkpoint the file as it is now. Returns None when unchanged.

        `reason` says who took it: "manual"/"snapshot" are the user's (kept
        until deleted); "before restore" is the one automatic copy. The
        default "save" is the legacy automatic reason and is pruned.

        `data` lets a caller snapshot bytes it already holds (a save that has
        not hit disk yet); otherwise the file is read.

        A label or description on identical bytes names the newest version
        when that adds to it (it has none yet, or the same one). When the
        newest version already carries a *different* label or description,
        nothing is overwritten: a new entry is added. It names the same
        stored file, so the bytes cost no further disk.

        When the file is read (no `data`) it is streamed through a temp file
        in the history folder while it is hashed, so a large archive is never
        held in memory; `progress(done, total)` follows those bytes and may be
        called from the calling thread only.
        """
        path = Path(file)
        staged: Path | None = None
        if data is not None:
            payload = data
            digest = hashlib.sha256(payload).hexdigest()
            size = len(payload)
        else:
            payload = None
            staged, digest, size = _stage_copy(path, self._folder(path),
                                               progress)
        try:
            return self._record_bytes(path, payload, staged, digest, size,
                                      reason, label, description)
        finally:
            if staged is not None:
                _discard(staged)      # a no-op once it was renamed into place

    def _record_bytes(self, path: Path, payload: bytes | None,
                      staged: Path | None, digest: str, size: int,
                      reason: str, label: str, description: str
                      ) -> Snapshot | None:
        label = clean_label(label)
        description = clean_description(description)
        with self._lock, _file_lock(self._folder(path) / "index.lock"):
            existing = self.snapshots(path)
            latest = existing[0] if existing else None
            if latest is not None and latest.sha256 == digest:
                clash = ((label and latest.label and latest.label != label)
                         or (description and latest.description
                             and latest.description != description))
                if not clash:
                    new_label = label or latest.label
                    new_desc = description or latest.description
                    if (new_label, new_desc) == (latest.label,
                                                 latest.description):
                        return None
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
            now = datetime.now(UTC)
            # The id is unique per entry; the stored name is the content
            # hash, so identical bytes share one file.
            sid = now.strftime("%Y%m%dT%H%M%S%f") + "-" + digest[:6]
            suffix = path.suffix or ".rbds"
            stored = digest + suffix
            folder = self._folder(path)
            target = folder / stored
            if not (target.is_file() and target.stat().st_size == size
                    and file_digest(target) == digest):
                if staged is not None:
                    os.replace(staged, target)
                else:
                    assert payload is not None
                    _atomic_write(target, payload)
            doc = self._read_index(path)
            entries = [e for e in doc.get("snapshots", [])
                       if isinstance(e, dict)]
            entries.append({
                "id": sid, "file": str(path), "created": now.isoformat(
                    timespec="seconds"),
                "reason": str(reason or "save"), "label": label,
                "bytes": size, "sha256": digest, "stored": stored,
                **({"description": description} if description else {}),
            })
            pending = [n for n in self._pending_deletions(doc)
                       if n != stored]
            doc, removed = self._prune(path, entries)
            if pending:
                doc["deleted"] = pending
            # The index is the truth: commit it first, then delete the
            # files it no longer names. A crash in between leaves an
            # unreferenced snapshot (recoverable) rather than an index
            # entry whose file is gone.
            self._write_index(path, doc)
            self._unlink(path, removed, doc["snapshots"], doc)
            return Snapshot(
                id=sid, file=str(path),
                created=now.isoformat(timespec="seconds"),
                reason=str(reason or "save"), label=label,
                bytes=size, sha256=digest, path=target,
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

    def usage(self, file: str | Path) -> int:
        """Bytes this model's history occupies on disk (shared files once)."""
        folder = self._folder(file)
        seen: dict[str, int] = {}
        for entry in self._read_index(file).get("snapshots", []):
            name = str(entry.get("stored") or "")
            if name and name not in seen and (folder / name).is_file():
                seen[name] = self._entry_size(folder, entry)
        return sum(seen.values())

    def _prune(self, file: str | Path, entries: list[dict[str, Any]]
               ) -> tuple[dict[str, Any], list[str]]:
        """Drop automatic copies beyond the limits; never an explicit one.

        Only entries `_is_automatic` says so are candidates: the newest
        `keep` of them survive, and they are dropped (oldest first) while
        the history's total, counting each stored file once, is over the
        byte cap. The newest automatic copy is always kept. Explicit
        versions are untouched however large the history gets; the dialog
        reports the size instead. Returns the new index and the stored
        names no remaining entry references, to delete after it is written.
        """
        folder = self._folder(file)
        entries = sorted(
            entries, key=lambda e: (str(e.get("created") or ""),
                                    str(e.get("id") or "")), reverse=True)
        sizes: dict[str, int] = {}
        for entry in entries:
            name = str(entry.get("stored") or "")
            if name and not _is_automatic(entry):
                sizes.setdefault(name, self._entry_size(folder, entry))
        kept: list[dict[str, Any]] = []
        automatic_kept = 0
        for entry in entries:
            if not _is_automatic(entry):
                kept.append(entry)
                continue
            name = str(entry.get("stored") or "")
            extra = 0 if name in sizes else self._entry_size(folder, entry)
            too_many = automatic_kept >= self.keep
            too_big = (sum(sizes.values()) + extra > self.max_bytes
                       and automatic_kept)
            if too_many or too_big:
                continue
            if name:
                sizes.setdefault(name, extra)
            kept.append(entry)
            automatic_kept += 1
        keep_ids = {id(e) for e in kept}
        dropped = [e for e in entries if id(e) not in keep_ids]
        return ({"version": INDEX_VERSION, "file": str(file),
                 "snapshots": kept},
                [str(e.get("stored") or "") for e in dropped
                 if e.get("stored")])

    @staticmethod
    def _pending_deletions(doc: dict[str, Any]) -> list[str]:
        raw = doc.get("deleted")
        return [str(n) for n in raw] if isinstance(raw, list) else []

    def _unlink(self, file: str | Path, names: list[str],
                remaining: list[dict[str, Any]],
                doc: dict[str, Any] | None = None) -> None:
        """Delete stored files that no remaining entry still names.

        A file that cannot be deleted (open on Windows) is recorded in the
        index's ``deleted`` list, so recovery never re-adds it as a version;
        the next write retries it.
        """
        folder = self._folder(file)
        in_use = {str(e.get("stored") or "") for e in remaining
                  if isinstance(e, dict)}
        pending = set(self._pending_deletions(doc or {})) | set(names)
        failed: set[str] = set()
        for name in sorted(pending):
            if name in in_use:
                continue
            try:
                (folder / name).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                failed.add(name)
        if doc is not None and failed != set(self._pending_deletions(doc)):
            if failed:
                doc["deleted"] = sorted(failed)
            else:
                doc.pop("deleted", None)
            try:
                self._write_index(file, doc)
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
            name = str(removed.get("stored") or "")
            if name and not any(isinstance(e, dict) and e.get("stored") == name
                                for e in kept):
                # Recorded before the unlink: if it fails, recovery must
                # not bring the deleted version back.
                doc["deleted"] = sorted(
                    {*self._pending_deletions(doc), name})
            self._write_index(file, doc)
            if name:
                self._unlink(file, [name], kept, doc)
            return True


def default_store() -> HistoryStore:
    return HistoryStore()

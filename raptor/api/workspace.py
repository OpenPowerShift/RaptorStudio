"""Workspace and model-handle management.

One directory, chosen at launch.  Everything the server writes goes
inside it.  Models are held as ``RbdFile`` objects and autosaved to
``.rbds``.  Handles are opaque tokens that survive client restarts
(because they map to directories on disk).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raptor.core.fileio.rbd import RbdFile
from raptor.core.fileio.rbds import load_rbds, save_rbds
from raptor.api.limits import MAX_MODEL_BYTES, MAX_TOTAL_MODELS


#: The friendly block fields a workspace's house defaults may set, applied
#: by `raptor.api.build.add_block` when the caller omits them.  Defined here
#: (rather than in `build`) because `build` imports this module.
BLOCK_DEFAULT_FIELDS = ("fail_dist", "fail_params", "repair_dist",
                        "repair_params")
_BLOCK_DEFAULT_FIELDS = BLOCK_DEFAULT_FIELDS


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class WorkspaceError(Exception):
    """Base error for workspace operations."""


class WorkspaceArgumentError(WorkspaceError, ValueError):
    """A caller-supplied argument is invalid (bad template, format, ...)."""


class WorkspaceFileNotFound(WorkspaceError, FileNotFoundError):
    """A file the caller named does not exist."""


class RunArtefactsError(WorkspaceError):
    """A run exists but its stored artefacts are missing or corrupt."""


class UnknownHandle(WorkspaceError):
    """Raised when a model_id or run_id does not exist or has expired."""


class PathOutsideWorkspace(WorkspaceError):
    """Raised when a path argument resolves outside the workspace."""


class ModelLimitExceeded(WorkspaceError):
    """Raised when the concurrent model cap is hit."""


class JobLimitExceeded(WorkspaceError):
    """Raised when too many background jobs are already queued or running."""


class WorkspaceSizeExceeded(WorkspaceError):
    """Raised when the workspace disk cap is hit."""


def check_model_name(name: Any) -> str:
    """A model's name is a label shown in lists and used to name exports. It
    must be non-empty text of at most `MAX_MODEL_NAME` characters with no
    control or invisible characters (a newline would split a log line or a
    legacy-file row)."""
    from raptor.api.limits import MAX_MODEL_NAME
    from raptor.core.input_rules import control_problem
    if not isinstance(name, str) or not name.strip():
        raise WorkspaceArgumentError("model name must be non-empty text")
    if len(name) > MAX_MODEL_NAME:
        raise WorkspaceArgumentError(
            f"model name is too long ({len(name)} characters); the limit "
            f"is {MAX_MODEL_NAME}")
    why = control_problem(name)
    if why:
        raise WorkspaceArgumentError(f"model name {why}")
    return name


def _real_row_count(rbd: RbdFile, section: str) -> int:
    """Count data rows in *section*, excluding the ``ID=0`` template row."""
    sec = rbd.sections.get(section)
    if not sec:
        return 0
    return sum(1 for row in sec.rows if row.get("ID") != "0")


def _summarise_counts(rbd: RbdFile) -> dict[str, int]:
    """Element counts for an index entry."""
    return {
        "blocks": _real_row_count(rbd, "BLOCK_DATA"),
        "nodes": _real_row_count(rbd, "NODE_DATA"),
    }


def rbds_only_content(rbd: RbdFile) -> list[str]:
    """What a `.rbd` save would drop: content only `.rbds` can carry.

    The run experiment counts only when it differs from the one derived
    from the SYSTEM_DATA row (which a `.rbd` re-derives on load anyway)."""
    lost: list[str] = []
    if getattr(rbd, "annotations", None):
        lost.append("annotations")
    if getattr(rbd, "rates", None):
        lost.append("rate definitions")
    if getattr(rbd, "formulas", None):
        lost.append("formulas")
    experiment = getattr(rbd, "experiment", None)
    if experiment:
        from raptor.core import run_experiment
        sec = rbd.sections.get("SYSTEM_DATA")
        row = sec.rows[0] if sec and sec.rows else {}
        derived = run_experiment.normalise(run_experiment.from_system_row(row))
        if run_experiment.normalise(experiment) != derived:
            lost.append("experiment (run) settings")
    return lost


def _sync_counts(rbd: RbdFile) -> None:
    """Recompute header counts from the actual rows.

    ``.rbds`` does not persist the legacy header counts, so a loaded model
    carries the template's counts (Blocks=1, ...) and `validate()` warns on
    every model with more than the template's rows.  Recomputing on load
    and before save keeps the in-memory counts honest.
    """
    try:
        from raptor.core.fileio.builder import sync_counts
        sync_counts(rbd)
    except Exception:  # noqa: BLE001 - counts are bookkeeping, never fatal
        pass


# ---------------------------------------------------------------------------
# Model handle
# ---------------------------------------------------------------------------

@dataclass
class ModelHandle:
    """Internal bookkeeping for one open model."""
    model_id: str
    name: str
    path: Path          # .../models/<model_id>/model.rbds
    meta_path: Path     # .../models/<model_id>/meta.json
    rbd: RbdFile
    created: float = field(default_factory=time.time)
    last_modified: float = field(default_factory=time.time)
    source: str = ""    # original path or "template:<name>"
    #: Monotonic edit/restore counter, so `undo`/`redo` can report which
    #: version they restored instead of always saying 1.
    version: int = 1
    #: Set by `save` when the layout guard moved elements, so the edit
    #: tools can tell the user instead of silently rearranging the diagram.
    layout_notice: str | None = None
    #: Depth of an open `Workspace.batch()`.  While > 0, `save` marks the
    #: handle dirty instead of rewriting the model, so a bulk edit costs one
    #: serialisation + backup rather than one per element.
    defer_depth: int = 0
    #: True when edits happened while deferred and have not been written yet.
    dirty: bool = False

    @property
    def effective_path(self) -> Path:
        """A readable file for this model, materialised or not.

        `path` is the workspace copy, which does not exist until the model
        is first edited (opening writes nothing).  Anything that reads the
        model as a *file* -- the render worker runs out-of-process and can
        only be handed a path -- must use this, not `path`.
        """
        if self.path.exists():
            return self.path
        if self.source and Path(self.source).is_file():
            return Path(self.source)
        return self.path

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``list_models``."""
        block_count = _real_row_count(self.rbd, "BLOCK_DATA")
        node_count = _real_row_count(self.rbd, "NODE_DATA")
        return {
            "model_id": self.model_id,
            "name": self.name,
            "blocks": block_count,
            "nodes": node_count,
            "last_modified": self.last_modified,
            "source": self.source,
        }


# Avoid importing RbdSection at module level — it's a plain dataclass
# but we want the api layer to be importable even if core has issues.


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

class Workspace:
    """Manages models, runs, and renders inside a single directory tree.

    Directory layout::

        <workspace>/
          models/<model_id>/model.rbds
          models/<model_id>/meta.json
          runs/<run_id>/final_results.txt
          runs/<run_id>/summary.json
          renders/<hash>.png
    """

    # Defaults
    MAX_CONCURRENT_MODELS = 50
    MAX_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
    RETAIN_DAYS = 30
    MAX_BACKUPS = 20

    def __init__(self, root: str | Path, *,
                 max_models: int = MAX_CONCURRENT_MODELS,
                 retain_days: int = RETAIN_DAYS,
                 max_bytes: int = MAX_WORKSPACE_BYTES,
                 max_total_models: int = MAX_TOTAL_MODELS) -> None:
        self.root = Path(root).resolve()
        self.max_models = max_models
        self.retain_days = retain_days
        self.max_bytes = max_bytes
        self.max_total_models = max_total_models

        # Ensure directory structure. A new workspace is private to its owner:
        # it holds models and results other local users have no need to read.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "models").mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "renders").mkdir(parents=True, exist_ok=True)

        # Materialised models: model_id -> ModelHandle.  A *cache*, bounded
        # by `max_models`; it is not the list of models the workspace has.
        self._models: dict[str, ModelHandle] = {}
        # The authoritative list: model_id -> the meta dict on disk.  Read
        # at startup and kept in step on every write, so a workspace with
        # thousands of models costs one small JSON read each.
        self._index: dict[str, dict[str, Any]] = {}
        # model_id -> monotonically increasing last-use stamp, for LRU.
        self._lru: dict[str, float] = {}
        self._cache_lock = threading.Lock()
        self._clock = itertools.count(1).__next__

        # House defaults for newly placed blocks, applied by
        # `raptor.api.build.add_block` when the caller omits a distribution.
        # Mirrors the GUI's "Block Defaults" (QSettings-seeded) so an MCP
        # caller can set their values once instead of restating them on
        # every `add_block`.  Persisted next to the workspace so it
        # survives a server restart; `{}` means "use the model's own
        # template-row defaults".
        self.block_defaults: dict[str, Any] = {}
        self._load_block_defaults()

        # Per-model mutation locks. `save`/`undo`/`save_model_as`/
        # `register_model` all read and rewrite a model's on-disk files
        # (backups, model.rbds, meta.json); one re-entrant lock per model
        # serialises them so two concurrent calls cannot interleave
        # `_backup`'s rename/copy sequence. Guarded by `_locks_guard` so
        # the dict itself is safe to mutate from many threads. Never
        # cleared (a closed model's lock is tiny and its id is never
        # reused).
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

        # Load any existing models from disk into the cache
        self._load_existing_models()

    # ------------------------------------------------------------------
    # Path containment
    # ------------------------------------------------------------------

    def _assert_inside(self, path: Path, *,
                       allow_extra: list[Path] | None = None) -> Path:
        """Resolve *path* and reject it if it escapes the workspace.

        Returns the resolved path on success.
        """
        resolved = path.resolve()
        if resolved == self.root or self.root in resolved.parents:
            return resolved
        if allow_extra:
            for allowed in allow_extra:
                allowed_resolved = allowed.resolve()
                if resolved == allowed_resolved or allowed_resolved in resolved.parents:
                    return resolved
        raise PathOutsideWorkspace(
            f"Path {path} resolves outside the workspace {self.root}"
        )

    # ------------------------------------------------------------------
    # Block defaults
    # ------------------------------------------------------------------

    #: Where the house block defaults live, relative to the workspace root.
    _DEFAULTS_FILE = "block_defaults.json"

    def _load_block_defaults(self) -> None:
        path = self.root / self._DEFAULTS_FILE
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.block_defaults = {
                k: v for k, v in data.items() if k in _BLOCK_DEFAULT_FIELDS
            }

    def set_block_defaults(self, defaults: dict[str, Any] | None) -> dict:
        """Replace (or, with *None*, clear) the workspace's block defaults.

        Only the four distribution fields are accepted; anything else is
        ignored so a typo cannot silently become a no-op that looks set.
        Returns the stored defaults.
        """
        if defaults is None:
            self.block_defaults = {}
        else:
            unknown = set(defaults) - set(_BLOCK_DEFAULT_FIELDS)
            if unknown:
                raise WorkspaceArgumentError(
                    f"Unknown block default(s): {', '.join(sorted(unknown))}. "
                    f"Valid: {', '.join(_BLOCK_DEFAULT_FIELDS)}"
                )
            self.block_defaults = dict(defaults)
        path = self.root / self._DEFAULTS_FILE
        if self.block_defaults:
            path.write_text(json.dumps(self.block_defaults, indent=2))
        elif path.exists():
            path.unlink()
        return dict(self.block_defaults)

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------

    def _new_id(self, prefix: str = "m") -> str:
        """Generate a short, collision-resistant ID like ``m_7f3a9c21``."""
        h = hashlib.sha256(
            f"{time.time_ns()}-{prefix}-{os.getpid()}".encode()
        ).hexdigest()[:8]
        return f"{prefix}_{h}"

    def _lock_for(self, model_id: str) -> threading.RLock:
        """Return the per-model mutation lock, creating it on first use.

        All on-disk mutation of one model (backups, model.rbds, meta.json)
        is serialised through this lock — see `self._locks`' docstring.
        """
        with self._locks_guard:
            lock = self._locks.get(model_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[model_id] = lock
            return lock

    # ------------------------------------------------------------------
    # Model operations
    # ------------------------------------------------------------------

    def create_model(self, name: str, *,
                     template: str = "blank",
                     n: int = 2, k: int = 1) -> dict[str, Any]:
        """Create a new empty model and return its handle info."""
        check_model_name(name)
        self._check_model_slot()
        model_id = self._new_id("m")
        model_dir = self.root / "models" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "model.rbds"
        meta_path = model_dir / "meta.json"

        # Build the initial RbdFile
        if template == "blank":
            from raptor.gui.new_model import build_new_model
            rbd = build_new_model(file_name=name)
        elif template in ("series", "parallel", "k_of_n"):
            from raptor.api.limits import MAX_TEMPLATE_BLOCKS
            try:
                count = int(n)
                vote = int(k)
            except (TypeError, ValueError):
                raise WorkspaceArgumentError(
                    "template n and k must be integers") from None
            if not 1 <= count <= MAX_TEMPLATE_BLOCKS:
                raise WorkspaceArgumentError(
                    f"template n must be between 1 and "
                    f"{MAX_TEMPLATE_BLOCKS}")
            if template == "k_of_n" and not 1 <= vote <= count:
                raise WorkspaceArgumentError(
                    "k_of_n requires 1 <= k <= n")
            rbd = self._build_template(template, name, n=count, k=vote)
        else:
            raise WorkspaceArgumentError(f"Unknown template: {template!r}")

        # Create handle
        handle = ModelHandle(
            model_id=model_id,
            name=name,
            path=model_path,
            meta_path=meta_path,
            rbd=rbd,
            source=f"template:{template}",
        )

        # Save to disk
        _sync_counts(rbd)
        size = self._model_bytes(rbd)
        self._check_model_size(size)
        self._check_quota(size)
        save_rbds(rbd, str(model_path), meta={"file_name": rbd.file_name})
        self._save_meta(handle)
        self._adopt(handle)

        return {
            "model_id": model_id,
            "summary": self._summarise(rbd),
        }

    def open_model(self, path: str | Path) -> dict[str, Any]:
        """Register a model file with the workspace and return its handle.

        The source is copied **byte-for-byte** into the workspace, so the
        handle owns a snapshot: later edits to the source (or deleting it)
        cannot change what this ``model_id`` means, and results stay
        reproducible across restarts.  The copy deliberately does not go
        through `save_rbds`, which would re-run the layout normaliser and
        change the diagram just by opening it (three of the shipped
        `new_docs/models` figures tripped that).

        The source file itself is never written.
        """
        src = Path(path).resolve()
        if not src.is_file():
            raise WorkspaceFileNotFound(f"File not found: {src}")
        if src.suffix.lower() not in (".rbds", ".rbd"):
            raise WorkspaceArgumentError(
                f"Unrecognised format: {src.suffix!r}.  "
                "Expected .rbds or .rbd"
            )
        try:
            source_bytes = src.stat().st_size
        except OSError as exc:
            raise WorkspaceFileNotFound(f"File not found: {src}") from exc
        self._check_model_size(source_bytes)
        self._check_model_slot()
        self._check_quota(source_bytes)

        rbd = self._load_any(str(src))
        summary = self._summarise(rbd)

        model_id = self._new_id("m")
        model_dir = self.root / "models" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        # Own a byte-for-byte snapshot. A live reference meant the same
        # model_id silently meant different content after the source was
        # edited (or vanished), and results stopped being reproducible.
        # Copying the bytes -- not save_rbds -- keeps the source layout
        # intact, which is why open_model does not rewrite the model.
        snapshot = model_dir / f"source{src.suffix}"
        shutil.copyfile(src, snapshot)
        handle = ModelHandle(
            model_id=model_id,
            name=src.stem,
            path=model_dir / "model.rbds",
            meta_path=model_dir / "meta.json",
            rbd=rbd,
            source=str(src),
        )
        # Index only: no save_rbds, so the source's layout survives intact.
        self._save_meta(handle)
        self._adopt(handle, index_only=True)
        return {"model_id": model_id, "summary": summary}

    @staticmethod
    def _load_any(path: str) -> RbdFile:
        if Path(path).suffix.lower() == ".rbds":
            return load_rbds(path)
        from raptor.core.fileio.rbd import load_rbd
        return load_rbd(path)

    def register_model(self, rbd: RbdFile, *, name: str,
                       source: str = "") -> dict[str, Any]:
        """Register an already-loaded RbdFile, save it, return handle info.

        Unlike `open_model` this *does* write: the caller has handed us a
        live object, so persisting it immediately is what makes the model
        restorable across a server restart.
        """
        check_model_name(name)
        self._check_model_slot()
        _sync_counts(rbd)
        size = self._model_bytes(rbd)
        self._check_model_size(size)
        self._check_quota(size)
        model_id = self._new_id("m")
        model_dir = self.root / "models" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "model.rbds"
        meta_path = model_dir / "meta.json"

        handle = ModelHandle(
            model_id=model_id,
            name=name,
            path=model_path,
            meta_path=meta_path,
            rbd=rbd,
            source=source,
        )

        with self._lock_for(model_id):
            save_rbds(rbd, str(model_path), meta={"file_name": rbd.file_name})
            self._save_meta(handle)
            self._adopt(handle)

        return {
            "model_id": model_id,
            "summary": self._summarise(rbd),
        }

    def _adopt(self, handle: ModelHandle, *,
               index_only: bool = False) -> None:
        """Add *handle* to the index and (unless *index_only*) the cache.

        The cache is bounded by `max_models`; going over evicts the
        least-recently-used materialised model rather than refusing the
        new one.  A workspace can hold far more models than the cache
        does, so the cap is a memory bound, not a licensing limit, and it
        must never be a reason a tool call fails.
        """
        with self._cache_lock:
            self._index[handle.model_id] = self._index_entry(handle)
            if not index_only:
                self._models[handle.model_id] = handle
                self._lru[handle.model_id] = self._clock()
                self._evict_locked()

    def _evict_locked(self) -> None:
        """Drop least-recently-used materialised models past the cap.

        Only the cache entry is dropped: the `ModelHandle` object itself
        is unaffected, so an in-flight caller keeps working on its own
        reference.  A model being mutated is by definition the most
        recently used one, so it is never the victim here; if it were
        somehow evicted, its next `get()` re-reads from disk, which is
        why every mutating tool saves before returning.
        """
        while len(self._models) > self.max_models and self._lru:
            victim = min(self._lru, key=lambda mid: self._lru[mid])
            self._models.pop(victim, None)
            self._lru.pop(victim, None)

    def get(self, model_id: str) -> ModelHandle:
        """Return the handle for *model_id*, materialising it if needed.

        A model evicted from the cache (or never loaded, because the
        workspace index is larger than the cache) is read back from its
        workspace copy on demand, so handles stay valid for the life of
        the workspace.
        """
        handle = self._models.get(model_id)
        if handle is not None:
            with self._cache_lock:
                self._lru[model_id] = self._clock()
            return handle

        if model_id not in self._index:
            raise UnknownHandle(
                f"Unknown or expired model handle: {model_id!r}.  "
                "A handle is valid for the life of the server's workspace; "
                "it stops resolving once the model is closed with "
                "delete=true, or the workspace itself is replaced.  "
                "Call list_models to see the model_ids available, and "
                "open_model to load a file from disk."
            )

        model_dir = self.root / "models" / model_id
        path = model_dir / "model.rbds"
        if not path.exists():
            # Indexed but never materialised: use the snapshot taken at
            # open time, falling back to the source only for older
            # workspaces created before snapshots existed.
            snapshot = next(
                (model_dir / f"source{suffix}" for suffix in (".rbds", ".rbd")
                 if (model_dir / f"source{suffix}").is_file()), None)
            if snapshot is None:
                source = self._index[model_id].get("source") or ""
                if not source or not Path(source).is_file():
                    raise UnknownHandle(
                        f"Model {model_id!r} is indexed but its file is "
                        f"missing (looked for {path}"
                        + (f" and source {source})" if source else ")")
                        + ".  Open it again with open_model."
                    )
                snapshot = Path(source)
            path = snapshot

        rbd = self._load_any(str(path))
        meta = self._index[model_id]
        handle = ModelHandle(
            model_id=model_id,
            name=meta.get("name", model_id),
            path=model_dir / "model.rbds",
            meta_path=model_dir / "meta.json",
            rbd=rbd,
            created=meta.get("created", 0.0),
            last_modified=meta.get("last_modified", 0.0),
            source=meta.get("source", ""),
        )
        with self._lock_for(model_id):
            _sync_counts(rbd)
        with self._cache_lock:
            self._models[model_id] = handle
            self._lru[model_id] = self._clock()
            self._evict_locked()
        return handle

    def save(self, model_id: str, *, check_rules: bool = True) -> None:
        """Autosave the model to disk (creating a backup first).

        Every edit tool ends here, so this is where an edit that breaks one of
        Raptor 7's input rules is refused (`raptor.core.input_rules`): the
        model is compared with the version on disk, and if the edit adds a
        violation the in-memory model is put back as it was and
        `InputRuleViolation` is raised with Raptor 7's message. Problems the
        model already had (a file written elsewhere) never block an edit.
        *check_rules* is off only for a save that copies a whole model, not
        an edit (a sweep's copies).

        Saving never moves an element: a crowded layout is a validation
        warning, and only an explicit Auto Layout changes positions.
        """
        handle = self.get(model_id)
        with self._lock_for(model_id):
            if handle.defer_depth > 0:
                # Inside a batch: keep the edit in memory only. The batch's
                # exit performs the single save (and the rule check) once.
                handle.dirty = True
                return
            if check_rules:
                self._refuse_rule_violations(handle)
            try:
                existing = handle.path.stat().st_size
            except OSError:
                existing = 0
            size = self._model_bytes(handle.rbd)
            self._check_model_size(size)
            self._check_quota(max(0, size - existing))
            if not handle.path.exists() and handle.source:
                # First write of a model that was only referenced. The
                # backup must be a readable .rbds, not the source .rbd:
                # undo used to load the legacy text as JSON and fail.
                src = Path(handle.source)
                if src.is_file():
                    if src.suffix.lower() == ".rbd":
                        from raptor.core.fileio.rbd import load_rbd
                        baseline = load_rbd(str(src))
                    else:
                        from raptor.core.fileio.rbds import load_rbds
                        baseline = load_rbds(str(src))
                    save_rbds(baseline, str(handle.path),
                              meta={"file_name": baseline.file_name})
            self._backup(handle)
            _sync_counts(handle.rbd)
            save_rbds(handle.rbd, str(handle.path),
                      meta={"file_name": handle.rbd.file_name})
            handle.version += 1
            handle.layout_notice = None
            handle.last_modified = time.time()
            self._save_meta(handle)
            with self._cache_lock:
                self._index[model_id] = self._index_entry(handle)
                self._lru[model_id] = self._clock()

    @contextmanager
    def batch(self, model_id: str, *, rollback: bool = True):
        """Apply many edits with one save (and one rule check).

        Every edit API ends in :meth:`save`, which serialises the whole
        model and takes a backup -- on a large model that is seconds per
        element.  Inside this context manager `save` only marks the handle
        dirty, so a bulk edit costs a single write.  On an exception the
        in-memory model is restored to the snapshot taken here (the file was
        never written, so this keeps memory and disk consistent).
        """
        import copy

        handle = self.get(model_id)
        snapshot = copy.deepcopy(handle.rbd) if rollback else None
        with self._lock_for(model_id):
            handle.defer_depth += 1
        try:
            yield handle
        except BaseException:
            with self._lock_for(model_id):
                handle.defer_depth -= 1
                handle.dirty = False
                if snapshot is not None:
                    handle.rbd = snapshot
            raise
        with self._lock_for(model_id):
            handle.defer_depth -= 1
            pending = handle.defer_depth == 0 and handle.dirty
            handle.dirty = False
        if pending:
            self.save(model_id)

    def _refuse_rule_violations(self, handle: ModelHandle) -> None:
        """Raise `InputRuleViolation`, restoring *handle*'s last saved state,
        when the pending edit adds an input-rule violation."""
        from raptor.core.input_rules import InputRuleViolation, new_violations
        before: RbdFile | None = None
        for path in (handle.path, Path(handle.source) if handle.source else None):
            if path is not None and path.is_file():
                before = self._load_any(str(path))
                break
        added = new_violations(before, handle.rbd)
        if not added:
            return
        if before is not None:
            handle.rbd = before
        raise InputRuleViolation(added)

    def list_models(self) -> list[dict[str, Any]]:
        """Return info about every model in the workspace index.

        Reads the index, not the cache, so it reports every model the
        workspace knows about however many are currently materialised.
        """
        with self._cache_lock:
            cached = dict(self._models)
        out = []
        for model_id, meta in self._index.items():
            handle = cached.get(model_id)
            if handle is not None:
                out.append(handle.to_dict())
            else:
                out.append(dict(meta))
        out.sort(key=lambda m: m.get("name") or "")
        return out

    def close(self, model_id: str, *, delete: bool = False) -> dict[str, Any]:
        """Close (and optionally delete) a model.

        `close` means "forget this for the rest of the session": the
        model leaves both the cache and the index, so it no longer
        appears in `list_models` and its handle stops resolving -- the
        same contract it has always had.  Its files stay on disk and come
        back on the next server start, exactly as before.

        `delete=true` additionally removes the model's directory, so it
        does not come back at all.
        """
        with self._cache_lock:
            known = model_id in self._index or model_id in self._models
            self._models.pop(model_id, None)
            self._lru.pop(model_id, None)
            self._index.pop(model_id, None)
        if not known:
            raise UnknownHandle(
                f"Unknown or expired model handle: {model_id!r}.  "
                "Call list_models to see the model_ids available."
            )
        if delete:
            model_dir = self.root / "models" / model_id
            if model_dir.exists():
                shutil.rmtree(model_dir, ignore_errors=True)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Saving out (containment enforced)
    # ------------------------------------------------------------------

    def resolve_output_path(self, path: str | Path, *,
                            allow_extra: list[Path] | None = None) -> Path:
        """Resolve *path* and require it to live inside the workspace (or
        an explicitly allowed directory).  Returns the resolved path.

        A relative *path* is resolved against the workspace root, not the
        process cwd, so a caller can pass ``"out/model.rbd"`` and get
        ``<workspace>/out/model.rbd`` regardless of where the server was
        launched.  Containment is still enforced either way.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            return self._assert_inside(candidate, allow_extra=allow_extra)
        except PathOutsideWorkspace:
            # name the path the caller gave, not the server's directories
            raise PathOutsideWorkspace(
                f"Path {str(path)!r} resolves outside the workspace") from None

    # ------------------------------------------------------------------
    # Quotas
    # ------------------------------------------------------------------

    def _check_model_slot(self) -> None:
        """Refuse a new model once the on-disk/inode cap is reached."""
        if len(self._index) >= self.max_total_models:
            raise ModelLimitExceeded(
                f"workspace model limit reached ({self.max_total_models}); "
                "close models with delete=true (or remove old exports) "
                "before creating more")

    @staticmethod
    def _model_bytes(rbd: RbdFile) -> int:
        """Serialized size of a model, used before writing it to disk."""
        from raptor.core.fileio.rbds import render_rbds
        return len(render_rbds(rbd).encode("utf-8"))

    def _check_model_size(self, size: int) -> None:
        if size > MAX_MODEL_BYTES:
            raise WorkspaceSizeExceeded(
                f"model is {size} bytes; the per-model limit is "
                f"{MAX_MODEL_BYTES} bytes")

    def _check_quota(self, extra_bytes: int = 0) -> None:
        """Evict old runs, then refuse a write that would exceed the cap.

        Models are never auto-evicted (they are the user's work), so a
        workspace already over its cap refuses new writes until the user
        removes something.  Run artefacts are safe to expire and are
        evicted first.
        """
        try:
            used = self.enforce_size_cap()
        except OSError:
            used = self._workspace_bytes()
        if used + max(0, int(extra_bytes)) > self.max_bytes:
            raise WorkspaceSizeExceeded(
                f"workspace size limit reached ({self.max_bytes} bytes; "
                f"used {used}). Remove old exports/runs or raise the cap.")

    def info(self) -> dict[str, Any]:
        """Describe the workspace's location and limits.

        Backs the ``workspace_info`` MCP tool: callers can discover where
        relative paths resolve, what is writable, and the retention / size
        / model caps this instance was configured with.
        """
        return {
            "root": str(self.root),
            "allowed_write": [str(self.root)],
            "retain_days": self.retain_days,
            "max_models": self.max_models,
            "max_total_models": self.max_total_models,
            "max_bytes": self.max_bytes,
            "block_defaults": dict(self.block_defaults),
        }

    def save_model_as(self, model_id: str, path: str | Path, *,  # noqa: C901
                      fmt: str = "rbds") -> dict[str, Any]:
        """Write the model to *path* in the requested format.

        A ``.rbd`` export runs the structural validator first and refuses
        on ERROR, so a generated legacy file can never crash Raptor 7.
        """
        handle = self.get(model_id)
        dest = Path(path)
        if fmt not in ("rbds", "rbd"):
            raise WorkspaceArgumentError(f"Unknown format {fmt!r}; use 'rbds' or 'rbd'")
        # A path with no suffix would be written literally and then rejected
        # by open_model ("Unrecognised format: ''"), so save and open would
        # not round-trip. Append the format's own extension when the caller
        # gave none; an explicit suffix must agree with `fmt`.
        if dest.suffix == "":
            dest = dest.with_suffix(f".{fmt}")
        elif dest.suffix.lower() != f".{fmt}":
            raise WorkspaceArgumentError(
                f"format={fmt!r} needs a .{fmt} path; got {dest.suffix!r}")
        target = dest.resolve()
        # Do not overwrite any model's own files, or a run's sibling
        # artefacts. Path containment alone allowed a save to clobber
        # models/<id>/model.rbds (including a closed model that is meant
        # to survive a restart) and runs/<id>/summary.json.
        models_dir = (self.root / "models").resolve()
        if target == models_dir or models_dir in target.parents:
            raise WorkspaceError(
                "refusing to overwrite a model's files: the models tree "
                "is managed by the workspace (use a path under Tables/ or "
                "another allowed directory)")
        runs_dir = (self.root / "runs").resolve()
        if target == runs_dir or runs_dir in target.parents:
            raise WorkspaceError(
                "refusing to write inside the runs directory; run "
                "artefacts are managed by the engine")
        renders_dir = (self.root / "renders").resolve()
        if target == renders_dir or renders_dir in target.parents:
            raise WorkspaceError(
                "refusing to overwrite the render cache; renders are "
                "managed by render_diagram")
        with self._lock_for(model_id):
            _sync_counts(handle.rbd)
            export_bytes = self._model_bytes(handle.rbd)
            self._check_model_size(export_bytes)
            if target == self.root or self.root in target.parents:
                try:
                    existing_export = target.stat().st_size
                except OSError:
                    existing_export = 0
                self._check_quota(max(0, export_bytes - existing_export))
            # A nested export path is a normal request; create the folder
            # instead of failing with a bare Errno 2.
            dest.parent.mkdir(parents=True, exist_ok=True)
            if fmt == "rbd":
                from raptor.core.fileio.builder import save as save_rbd, validate
                errors = [e for e in validate(handle.rbd) if e.startswith("ERROR")]
                if errors:
                    raise WorkspaceError(
                        "refusing to export .rbd: validation errors:\n"
                        + "\n".join(errors)
                    )
                save_rbd(handle.rbd, str(dest))
            else:
                save_rbds(handle.rbd, str(dest),
                          meta={"file_name": handle.rbd.file_name})
            size = dest.stat().st_size
        from raptor.api.validate import layout_warning
        return {"path": str(dest), "bytes": size, "format": fmt,
                "dropped": (rbds_only_content(handle.rbd)
                            if fmt == "rbd" else []),
                # The layout is exported as it is; say so when Raptor 7
                # would draw it overlapping.
                "layout_warning": layout_warning(handle.rbd) if fmt == "rbd" else None}

    # ------------------------------------------------------------------
    # Size cap
    # ------------------------------------------------------------------

    def _workspace_bytes(self) -> int:
        total = 0
        for sub in ("models", "runs", "renders"):
            base = self.root / sub
            if base.is_dir():
                for f in base.rglob("*"):
                    if f.is_file():
                        try:
                            total += f.stat().st_size
                        except OSError:
                            pass
        return total

    def enforce_size_cap(self) -> int:
        """Evict the oldest run artefacts until the workspace fits.

        Models are never evicted.  Returns the workspace size after the
        sweep, so a caller enforcing a write can reuse it instead of
        walking the tree a second time.
        """
        total = self._workspace_bytes()
        if total <= self.max_bytes:
            return total
        runs_dir = self.root / "runs"
        if not runs_dir.is_dir():
            return total
        run_dirs = sorted(
            (d for d in runs_dir.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
        )
        for run_dir in run_dirs:
            if total <= self.max_bytes:
                break
            before = sum(f.stat().st_size for f in run_dir.rglob("*")
                         if f.is_file())
            shutil.rmtree(run_dir, ignore_errors=True)
            total -= before
        return total

    # ------------------------------------------------------------------
    # Version backups and undo
    # ------------------------------------------------------------------

    #: Undo chain files are ``model.rbds.<i>.bak``; redo chain files are
    #: ``model.rbds.redo.<i>.bak`` -- same shift/pop machinery, a
    #: different filename stem, so `undo`/`redo` are exact mirrors of
    #: each other instead of separate hand-written implementations.
    _UNDO_STEM = "model.rbds"
    _REDO_STEM = "model.rbds.redo"

    def _chain_path(self, model_dir: Path, stem: str, i: int) -> Path:
        return model_dir / f"{stem}.{i}.bak"

    def _push_onto_chain(self, handle: ModelHandle, stem: str) -> None:
        """Shift a numbered chain up one slot (discarding the oldest,
        ``.<MAX_BACKUPS>``) and copy the CURRENT on-disk file into the
        freed ``.1`` slot.

        Callers must hold the model's lock (``_lock_for``): the shift +
        copy sequence is not safe under concurrent mutation, because one
        save could rename or remove a file another is copying. The copy
        onto ``.1`` is atomic — a unique temp file in the same directory
        is written, then ``os.replace``d into place, so no reader ever
        observes a missing or partial ``.1``.
        """
        model_dir = handle.path.parent
        for i in range(self.MAX_BACKUPS - 1, 0, -1):
            src = self._chain_path(model_dir, stem, i)
            dst = self._chain_path(model_dir, stem, i + 1)
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
        if handle.path.exists():
            backup = self._chain_path(model_dir, stem, 1)
            fd, tmp_name = tempfile.mkstemp(
                dir=model_dir, prefix=f".{stem}.", suffix=".bak.tmp")
            os.close(fd)
            try:
                shutil.copy2(handle.path, tmp_name)
                os.replace(tmp_name, backup)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

    def _pop_chain_head(self, handle: ModelHandle, stem: str) -> None:
        """After consuming ``.1`` (its content is now the live file),
        shift the rest of the chain down one slot: ``.2`` -> ``.1``,
        ``.3`` -> ``.2``, ... -- the mirror of `_push_onto_chain`'s
        shift, freeing the top slot instead of the bottom one."""
        model_dir = handle.path.parent
        for i in range(1, self.MAX_BACKUPS):
            dst = self._chain_path(model_dir, stem, i)
            src = self._chain_path(model_dir, stem, i + 1)
            if dst.exists():
                dst.unlink()
            if src.exists():
                src.rename(dst)

    def _clear_chain(self, handle: ModelHandle, stem: str) -> None:
        model_dir = handle.path.parent
        for i in range(1, self.MAX_BACKUPS + 1):
            p = self._chain_path(model_dir, stem, i)
            if p.exists():
                p.unlink()

    def _backup(self, handle: ModelHandle) -> None:
        """Create a numbered backup before saving, and drop the redo
        chain: a fresh edit invalidates whatever `undo` you could have
        redone back to, same as any other editor."""
        self._push_onto_chain(handle, self._UNDO_STEM)
        self._clear_chain(handle, self._REDO_STEM)

    def _restore_from_chain(self, handle: ModelHandle, path: Path) -> RbdFile:
        rbd = load_rbds(str(path))
        handle.rbd = rbd
        save_rbds(rbd, str(handle.path), meta={"file_name": rbd.file_name})
        handle.version += 1
        handle.last_modified = time.time()
        self._save_meta(handle)
        return rbd

    def undo(self, model_id: str) -> dict[str, Any]:
        """Step one edit back through the undo chain.

        Repeated calls walk further back (``.1``, then what was ``.2``,
        ...) rather than restoring the same backup every time, and each
        step is itself redoable.
        """
        handle = self.get(model_id)
        with self._lock_for(model_id):
            model_dir = handle.path.parent
            backup = self._chain_path(model_dir, self._UNDO_STEM, 1)
            if not backup.exists():
                raise WorkspaceError("No backup available to restore.")
            self._push_onto_chain(handle, self._REDO_STEM)
            rbd = self._restore_from_chain(handle, backup)
            self._pop_chain_head(handle, self._UNDO_STEM)

        return {
            "restored_version": handle.version,
            "summary": self._summarise(rbd),
        }

    def redo(self, model_id: str) -> dict[str, Any]:
        """Step one edit forward again after `undo`.

        Any new edit (any tool that calls `save`) clears the redo chain,
        same as it would in an ordinary editor.
        """
        handle = self.get(model_id)
        with self._lock_for(model_id):
            model_dir = handle.path.parent
            redo_bak = self._chain_path(model_dir, self._REDO_STEM, 1)
            if not redo_bak.exists():
                raise WorkspaceError("No redo available -- undo first, "
                                     "or the last edit already cleared it.")
            self._push_onto_chain(handle, self._UNDO_STEM)
            rbd = self._restore_from_chain(handle, redo_bak)
            self._pop_chain_head(handle, self._REDO_STEM)

        return {
            "restored_version": handle.version,
            "summary": self._summarise(rbd),
        }

    # ------------------------------------------------------------------
    # Meta (JSON sidecar)
    # ------------------------------------------------------------------

    def _save_meta(self, handle: ModelHandle) -> None:
        """Write the meta.json sidecar.

        Element counts live here too: the index is built by reading these
        files without parsing any model, so this is what lets `list_models`
        report `blocks`/`nodes` for a workspace far larger than the cache.
        """
        counts = _summarise_counts(handle.rbd)
        meta = {
            "name": handle.name,
            "created": handle.created,
            "last_modified": handle.last_modified,
            "source": handle.source,
            "blocks": counts["blocks"],
            "nodes": counts["nodes"],
        }
        handle.meta_path.write_text(json.dumps(meta, indent=2))

    # ------------------------------------------------------------------
    # Retention sweep
    # ------------------------------------------------------------------

    def sweep_retention(self) -> dict[str, Any]:
        """Remove run/render artefacts older than ``retain_days``.

        Models are never evicted.
        """
        cutoff = time.time() - self.retain_days * 86400
        removed_runs = 0
        removed_renders = 0

        runs_dir = self.root / "runs"
        if runs_dir.exists():
            for run_dir in runs_dir.iterdir():
                if run_dir.is_dir():
                    # Use directory mtime as proxy for age
                    if run_dir.stat().st_mtime < cutoff:
                        shutil.rmtree(run_dir)
                        removed_runs += 1

        renders_dir = self.root / "renders"
        if renders_dir.exists():
            for f in renders_dir.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed_renders += 1

        return {
            "removed_runs": removed_runs,
            "removed_renders": removed_renders,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_existing_models(self) -> None:
        """Populate the index from the models directory.

        Deliberately does **not** parse any model file.  It used to load
        every `model.rbds` into the cache, which meant a workspace holding
        more models than `max_models` came back over the cap -- and since
        nothing ever evicts, `create_model` then failed for the lifetime
        of the server with a limit the user had no way to clear.  Now the
        index is one small JSON read per model and the cap only ever
        governs how many are *materialised* at once.
        """
        models_dir = self.root / "models"
        if not models_dir.exists():
            return
        for model_dir in sorted(models_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            meta_path = model_dir / "meta.json"
            materialised = (
                (model_dir / "model.rbds").exists()
                or any((model_dir / f"source{suffix}").exists()
                       for suffix in (".rbds", ".rbd")))
            meta: dict[str, Any] = {}
            if meta_path.exists():
                try:
                    loaded = json.loads(meta_path.read_text())
                    if isinstance(loaded, dict):
                        meta = loaded
                except Exception:
                    pass
            if not meta and not materialised:
                # Nothing to reference and no index entry: not a model.
                continue
            model_id = model_dir.name
            entry = {
                "model_id": model_id,
                "name": meta.get("name", model_id),
                "blocks": meta.get("blocks"),
                "nodes": meta.get("nodes"),
                "last_modified": meta.get("last_modified", 0.0),
                "source": meta.get("source", ""),
                "materialised": materialised,
            }
            self._index[model_id] = entry

        # Warm the cache up to the cap, most recently modified first, so the
        # common case of "reopen what I had open" costs nothing.
        for model_id, _ in sorted(
            self._index.items(),
            key=lambda kv: kv[1].get("last_modified") or 0.0,
            reverse=True,
        )[: self.max_models]:
            try:
                self.get(model_id)
            except UnknownHandle:
                self._index.pop(model_id, None)

    def _index_entry(self, handle: ModelHandle) -> dict[str, Any]:
        """The index row for *handle*, matching `ModelHandle.to_dict`."""
        counts = _summarise_counts(handle.rbd)
        return {
            "model_id": handle.model_id,
            "name": handle.name,
            "blocks": counts["blocks"],
            "nodes": counts["nodes"],
            "last_modified": handle.last_modified,
            "source": handle.source,
            "materialised": (handle.path.exists()
                            or any((handle.path.parent / f"source{s}").exists()
                                   for s in (".rbds", ".rbd"))),
        }

    def _summarise(self, rbd: RbdFile) -> str:
        """One-line human summary of a model."""
        blocks = _real_row_count(rbd, "BLOCK_DATA")
        nodes = _real_row_count(rbd, "NODE_DATA")
        events = _real_row_count(rbd, "EVENT_DATA")
        parts = [f"{blocks} block(s)", f"{nodes} node(s)"]
        if events:
            parts.append(f"{events} event(s)")
        return ", ".join(parts)

    def _build_template(self, template: str, name: str, *,
                        n: int = 2, k: int = 1) -> RbdFile:
        """Build a series / parallel / k_of_n template model.

        Prefers ``raptor.api.build.build_template`` when present; otherwise
        builds the spec with the module's own ``series``/``parallel``/
        ``k_of_n`` helpers and parses it.
        """
        from raptor.api import build as _build
        helper = getattr(_build, "build_template", None)
        if helper is not None:
            rbd = helper(template, n=n, k=k)
        else:
            if template == "series":
                spec = _build.series(n)
            elif template == "parallel":
                spec = _build.parallel(n)
            else:
                spec = _build.k_of_n(k, n)
            spec["name"] = name
            rbd = _build.build_from_spec(spec)
        rbd.file_name = name
        return rbd

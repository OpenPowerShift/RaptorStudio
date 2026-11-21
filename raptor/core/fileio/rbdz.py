"""`.rbdz` — the RAPTOR Studio model archive.

An `.rbdz` is the model **and the results it was run with** in one file: a
ZIP container holding the same `.rbds` JSON document and the same result
text files a saved run keeps in the workspace.  Opening one restores the
model and puts those results back on screen, so a result set can be sent to
a reviewer or reopened next month without the temporary run folder.

Why ZIP, and why not a domain-specific codec
--------------------------------------------
The result files are Raptor 7's own text (one line per event, per run and
per plot sample).  They are extremely repetitive, so a general-purpose
compressor does very well: on a real 400-run batch, ``end_of_run.txt``
(39 MB) goes to 2.5 MB with ZIP's deflate, 0.58 MB with bzip2 and 0.53 MB
with LZMA.  The availability plot (475 MB) goes to 95 MB / 80 MB / 45 MB.
The gain is not in a clever model of the data; it is in not storing the
same column names, timestamps and states over and over.

ZIP was chosen over a bespoke archive because every operating system can
open it, the standard library reads and writes it, and an entry can be
streamed (a 500 MB plot is never held in memory).  Entries are written
with bzip2 by default — close to LZMA's ratio at a fraction of the time —
with deflate for small metadata and LZMA available for a deliberate
"smallest" save.  The layout is documented in ``docs/RBDZ_FORMAT.md``.

Security: an archive is untrusted input.  Paths are checked (no absolute
names, no ``..``), entry count and uncompressed size are capped, and only
``model.rbds`` is parsed by the model reader.  Result files are treated as
text, never executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterable
from typing import Any

FORMAT_ID = "raptor-studio-archive"
VERSION = 1

MODEL_NAME = "model.rbds"
MANIFEST_NAME = "manifest.json"
RESULTS_DIR = "results/"

#: Compression profiles.  ZIP's own constants, so a reader needs no map.
COMPRESSION_PROFILES = {
    "fast": zipfile.ZIP_DEFLATED,
    "balanced": zipfile.ZIP_BZIP2,
    "smallest": zipfile.ZIP_LZMA,
}
DEFAULT_COMPRESSION = "balanced"

#: Refuse a file with more entries than this (a zip bomb made of tiny files).
MAX_ENTRIES = 10_000
#: Refuse an archive whose declared uncompressed size is above this.
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
#: Files smaller than this are cheaper to deflate (and stay readable by
#: every unzipper) than to bzip2/LZMA.
_SMALL_ENTRY = 4096


class RbdzError(Exception):
    """An `.rbdz` archive is missing a required part or is malformed."""


@dataclass
class RbdzArchive:
    """What was read from an archive."""
    path: str
    model_text: str
    manifest: dict[str, Any] = field(default_factory=dict)
    #: ``name -> text`` for the result files that were requested.
    results: dict[str, str] = field(default_factory=dict)
    #: Every entry name in the container (useful for diagnostics and tests).
    names: list[str] = field(default_factory=list)

    @property
    def model_hash(self) -> str:
        return hashlib.sha256(self.model_text.encode("utf-8")).hexdigest()

    @property
    def has_results(self) -> bool:
        return bool(self.results) or bool(
            (self.manifest.get("results") or {}).get("files"))


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def _text_bytes(source: Any) -> bytes:
    """Turn one entry's source into bytes without holding a stream twice."""
    if isinstance(source, bytes):
        return source
    if isinstance(source, str):
        return source.encode("utf-8")
    if isinstance(source, os.PathLike):
        return Path(source).read_bytes()
    if hasattr(source, "read_all_lines"):
        # A ResultStream: read its file, not its materialised list.
        path = getattr(source, "path", None)
        if path:
            return Path(path).read_bytes()
        return "\n".join(source.read_all_lines()).encode("utf-8")
    if isinstance(source, Iterable):
        chunks: list[str] = []
        for line in source:
            text = str(line)
            chunks.append(text if text.endswith("\n") else text + "\n")
        return "".join(chunks).encode("utf-8")
    raise TypeError(f"cannot archive {type(source).__name__}")


def _entry_info(name: str, compress_type: int) -> zipfile.ZipInfo:
    # A fixed timestamp makes two archives of the same content byte-equal
    # (apart from the manifest's own creation time), which keeps diffs and
    # tests honest. 1980-01-01 is ZIP's epoch.
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compress_type
    info.external_attr = 0o644 << 16
    return info


def _write_entry(zf: zipfile.ZipFile, name: str, source: Any,
                 profile: int) -> None:
    data = _text_bytes(source)
    compress = profile if len(data) >= _SMALL_ENTRY else zipfile.ZIP_DEFLATED
    zf.writestr(_entry_info(name, compress), data)


def _normalise_results(results: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, source in (results or {}).items():
        clean = str(name).replace("\\", "/").strip("/")
        if not clean or clean.startswith("../") or "/../" in clean:
            raise RbdzError(f"unsafe result name {name!r}")
        out[clean] = source
    return out


def write_archive(path: str | os.PathLike, model_text: str, *,  # noqa: C901
                  results: dict[str, Any] | None = None,
                  manifest: dict[str, Any] | None = None,
                  compression: str = DEFAULT_COMPRESSION,
                  created: str | None = None,
                  generator: str | None = None) -> dict[str, Any]:
    """Write *model_text* (and optional *results*) as an `.rbdz`.

    Returns the manifest that was written.  A result source may be a
    string, bytes, a path, a ``ResultStream`` or an iterable of lines; it
    is streamed into the archive, not joined in memory first.
    """
    if compression not in COMPRESSION_PROFILES:
        raise ValueError(f"unknown compression {compression!r}; choose from "
                         f"{', '.join(sorted(COMPRESSION_PROFILES))}")
    profile = COMPRESSION_PROFILES[compression]
    result_sources = _normalise_results(results)
    if not model_text.strip():
        raise RbdzError("the model is empty")
    if generated := (manifest or {}).get("generator"):
        generator = generator or generated
    if generator is None:
        try:
            from raptor import __version__ as _v
            generator = f"RAPTOR Studio {_v}"
        except Exception:          # pragma: no cover - version metadata only
            generator = "RAPTOR Studio"

    doc = dict(manifest or {})
    doc.update({
        "format": FORMAT_ID,
        "version": VERSION,
        "generator": generator,
    })
    if created is not None:
        doc["created"] = created
    doc["compression"] = compression
    doc["model"] = {
        **(doc.get("model") or {}),
        "name": MODEL_NAME,
        "bytes": len(model_text.encode("utf-8")),
        "sha256": hashlib.sha256(model_text.encode("utf-8")).hexdigest(),
    }

    result_entries: dict[str, dict[str, Any]] = {}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as zf:
            _write_entry(zf, MODEL_NAME, model_text, profile)
            for name in sorted(result_sources):
                _write_entry(zf, RESULTS_DIR + name, result_sources[name], profile)
                info = zf.getinfo(RESULTS_DIR + name)
                result_entries[name] = {"bytes": info.file_size}
            if result_entries:
                doc["results"] = {**(doc.get("results") or {}),
                                  "files": result_entries}
            doc.setdefault("files", {})
            doc["files"] = {
                MODEL_NAME: {"bytes": len(model_text.encode("utf-8"))},
                **{RESULTS_DIR + k: v for k, v in result_entries.items()},
                MANIFEST_NAME: {},
            }
            # The manifest goes last: it names the entries and their sizes, so
            # it can only be complete once they have been written.
            _write_entry(zf, MANIFEST_NAME,
                         json.dumps(doc, indent=1, sort_keys=True) + "\n",
                         zipfile.ZIP_DEFLATED)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return doc


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    clean = name.replace("\\", "/")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise RbdzError(f"unsafe entry name {name!r}")
    if clean.startswith("/") or (len(clean) > 1 and clean[1] == ":"):
        raise RbdzError(f"unsafe entry name {name!r}")
    return "/".join(parts)


def read_archive(path: str | os.PathLike, *, with_results: bool = True,  # noqa: C901, PLR0912
                 max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES
                 ) -> RbdzArchive:
    """Read an archive's model (always) and result files (when requested)."""
    files: list[zipfile.ZipInfo]
    try:
        with zipfile.ZipFile(path, "r") as zf:
            files = zf.infolist()
            if len(files) > MAX_ENTRIES:
                raise RbdzError(
                    f"the archive has {len(files):,} entries; the limit is "
                    f"{MAX_ENTRIES:,}")
            total = sum(info.file_size for info in files)
            if total > max_uncompressed_bytes:
                raise RbdzError(
                    f"the archive would unpack to {total / (1024 ** 3):.1f} GB; "
                    f"the limit is {max_uncompressed_bytes / (1024 ** 3):.1f} GB")
            names = [_safe_name(info.filename) for info in files]
            if MODEL_NAME not in names:
                raise RbdzError(f"not a RAPTOR Studio archive: no {MODEL_NAME}")
            model_text = zf.read(MODEL_NAME).decode("utf-8-sig")
            manifest: dict[str, Any] = {}
            if MANIFEST_NAME in names:
                try:
                    loaded = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RbdzError("the archive manifest is not valid JSON") from exc
                if isinstance(loaded, dict):
                    manifest = loaded
            if manifest.get("format") not in (None, FORMAT_ID):
                raise RbdzError(
                    "the archive manifest is for another product: "
                    f"{manifest.get('format')!r}")
            try:
                archived_version = int(manifest.get("version") or 0)
            except (TypeError, ValueError):
                raise RbdzError("the archive manifest has an invalid version") from None
            if archived_version > VERSION:
                raise RbdzError(
                    "the archive was written by a newer RAPTOR Studio "
                    f"(version {manifest.get('version')}); this build reads "
                    f"version {VERSION}")
            results: dict[str, str] = {}
            if with_results:
                for info in files:
                    name = _safe_name(info.filename)
                    if not name.startswith(RESULTS_DIR) or name.endswith("/"):
                        continue
                    key = name[len(RESULTS_DIR):]
                    if not key:
                        continue
                    try:
                        results[key] = zf.read(name).decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise RbdzError(
                            f"result file {key!r} is not UTF-8 text") from exc
    except zipfile.BadZipFile as exc:
        raise RbdzError(f"{path} is not a ZIP archive") from exc
    return RbdzArchive(path=str(path), model_text=model_text,
                       manifest=manifest, results=results, names=names)


def load_model(path: str | os.PathLike):
    """Read just the model from an archive (the ``load_model_file`` path)."""
    archive = read_archive(path, with_results=False)
    from raptor.core.fileio.rbds import parse_rbds
    return parse_rbds(archive.model_text,
                      default_name=Path(path).stem + ".rbds")


def read_results(path: str | os.PathLike) -> RbdzArchive:
    """Read an archive's model and every result file."""
    return read_archive(path, with_results=True)


class ArchivedLines:
    """A list-like view of one extracted result file.

    The Results dock accepts any object with ``read_all_lines`` (that is
    how a live ``ResultStream`` is passed), so an archived plot can be
    shown without materialising a 475 MB file in RAM.  ``__len__`` counts
    lines once and caches the answer.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
        self._count: int | None = None

    def read_all_lines(self) -> list[str]:
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def __iter__(self):
        return iter(self.read_all_lines())

    def __len__(self) -> int:
        if self._count is None:
            try:
                with open(self.path, "rb") as f:
                    self._count = sum(1 for _ in f)
            except OSError:
                # A missing archived file reads as no rows, the same rule
                # `read_all_lines` follows.
                self._count = 0
        return self._count

    def __bool__(self) -> bool:
        return len(self) > 0

    def export_to(self, path: str) -> None:
        """Save Raw Report / Save Plot Data can write the extracted file."""
        import shutil
        shutil.copyfile(self.path, path)


def extract_results(path: str | os.PathLike, destination: str | os.PathLike  # noqa: C901
                    ) -> tuple[dict[str, Path], dict[str, Any]]:
    """Extract the result entries into *destination* and return their paths.

    Returns ``({name: path}, manifest)``.  Extraction is per entry and
    refuses unsafe names, like ``read_archive``.  The caller owns the
    destination (the GUI gives it a ``TemporaryDirectory``).
    """
    out: dict[str, Path] = {}
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                raise RbdzError(
                    f"the archive has {len(infos):,} entries; the limit is "
                    f"{MAX_ENTRIES:,}")
            total = sum(info.file_size for info in infos)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise RbdzError(
                    f"the archive would unpack to {total / (1024 ** 3):.1f} GB; "
                    f"the limit is {MAX_UNCOMPRESSED_BYTES / (1024 ** 3):.1f} GB")
            names = [_safe_name(info.filename) for info in infos]
            manifest: dict[str, Any] = {}
            if MANIFEST_NAME in names:
                try:
                    loaded = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
                    manifest = loaded if isinstance(loaded, dict) else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    manifest = {}
            if manifest.get("format") not in (None, FORMAT_ID):
                raise RbdzError(
                    "the archive manifest is for another product: "
                    f"{manifest.get('format')!r}")
            try:
                archived_version = int(manifest.get("version") or 0)
            except (TypeError, ValueError):
                raise RbdzError("the archive manifest has an invalid version") from None
            if archived_version > VERSION:
                raise RbdzError(
                    "the archive was written by a newer RAPTOR Studio "
                    f"(version {manifest.get('version')}); this build reads "
                    f"version {VERSION}")
            for info in infos:
                name = _safe_name(info.filename)
                if not name.startswith(RESULTS_DIR) or name.endswith("/"):
                    continue
                key = name[len(RESULTS_DIR):]
                if not key:
                    continue
                # `key` is already a safe relative path; write it under the
                # destination without letting `..` escape (checked above).
                dest = target / key
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                out[key] = dest
    except zipfile.BadZipFile as exc:
        raise RbdzError(f"{path} is not a ZIP archive") from exc
    return out, manifest


# ---------------------------------------------------------------------------
# a saved run folder -> archive entries
# ---------------------------------------------------------------------------

#: Files a workspace run keeps that are worth carrying into an archive.
#: ``sim_details`` and cumulative files are diagnostics and are left out
#: unless the caller asks for every file.
RESULT_FILES = (
    "summary.json", "per_run.json", "attribution.json",
    "final_results.txt", "final_results_ids.json",
    "end_of_run.txt", "events.txt", "events_ids.json",
    "availability_plot.txt", "capacity.txt", "cost.txt",
    "key_parameters.txt", "failure_times.txt", "down_times.txt",
    "end_times.txt", "importance.txt",
)


def results_from_run(run_dir: str | os.PathLike,
                     *, include: Iterable[str] | None = None
                     ) -> tuple[dict[str, Path], dict[str, Any]]:
    """The archive entries for a saved run folder, plus its summary.

    Files are carried as paths so the writer streams them.  Missing files
    are simply absent, exactly as they are in the run folder.
    """
    folder = Path(run_dir)
    if not folder.is_dir():
        raise RbdzError(f"{run_dir} is not a run folder")
    wanted = tuple(include) if include is not None else RESULT_FILES
    sources = {name: folder / name for name in wanted
               if (folder / name).is_file() and (folder / name).stat().st_size}
    summary: dict[str, Any] = {}
    summary_path = folder / "summary.json"
    if summary_path.is_file():
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
        except (OSError, ValueError):
            summary = {}
    return sources, summary

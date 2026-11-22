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
streamed: a 475 MB plot is copied through a fixed-size buffer in both
directions, never held in memory.  Entries are written with bzip2 by
default — close to LZMA's ratio at a fraction of the time — with deflate
for small metadata and LZMA available for a deliberate "smallest" save.
The layout is documented in ``docs/RBDZ_FORMAT.md``.

Security: an archive is untrusted input.  Paths are checked (no absolute
names, no ``..``, no drive-relative or ``:`` components), entry count and
uncompressed size are capped, and only ``model.rbds`` is parsed by the
model reader.  Result files are treated as text, never executed.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import uuid
import zipfile
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_ID = "raptor-studio-archive"
#: ``results/summary.json``'s own format id (not the archive's).
RESULTS_FORMAT_ID = "raptor-studio-results"
VERSION = 1
RESULTS_VERSION = 2

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
#: The model itself is held in RAM; keep it under the API's model cap.
MAX_MODEL_BYTES = 64 * 1024 * 1024
#: The manifest is small metadata; a huge one is malformed, not a plan.
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
#: Files smaller than this are cheaper to deflate (and stay readable by
#: every unzipper) than to bzip2/LZMA.
_SMALL_ENTRY = 4096
#: Copy buffer for a streamed entry (and for extraction).
_CHUNK = 1024 * 1024
#: A local ZIP header's 32-bit size limit; pass it to force a Zip64 header.
_ZIP64_LIMIT = (1 << 31) - 1

#: Failures a damaged or unsupported entry can raise while being read:
#: zlib/LZMA streams report their own errors, and bzip2 raises `OSError`.
_READ_ERRORS = (zipfile.BadZipFile, RuntimeError, NotImplementedError,
                EOFError, OSError, zlib.error, lzma.LZMAError)


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
        """True when the container (or its manifest) names result files."""
        if self.results:
            return True
        results = self.manifest.get("results")
        files = results.get("files") if isinstance(results, dict) else None
        return bool(files)


# ---------------------------------------------------------------------------
# entry names
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Normalise one entry name and refuse anything that could escape.

    Both directions use this rule, so the writer cannot create an entry its
    own reader would reject.  Absolute names, ``..`` components, NUL bytes
    and ``:`` components (a Windows drive or an NTFS alternate data stream
    once the name is joined under an extraction folder) are refused.
    """
    clean = str(name).replace("\\", "/")
    if not clean or clean.startswith("/") or "\x00" in clean:
        raise RbdzError(f"unsafe entry name {name!r}")
    parts: list[str] = []
    for part in clean.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or ":" in part:
            raise RbdzError(f"unsafe entry name {name!r}")
        parts.append(part)
    if not parts:
        raise RbdzError(f"unsafe entry name {name!r}")
    return "/".join(parts)


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def _iter_path(path: Any) -> Iterator[bytes]:
    """Yield a file's bytes through the fixed-size buffer."""
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(_CHUNK):
                yield chunk
    except OSError as exc:
        raise RbdzError(f"cannot read {path}: {exc}") from exc


def _iter_lines(lines: Iterable[Any]) -> Iterator[bytes]:
    for line in lines:
        text = str(line)
        yield (text if text.endswith("\n") else text + "\n").encode("utf-8")


def _iter_source(source: Any) -> Iterator[bytes]:
    """Yield one entry's bytes in chunks, without materialising a file.

    A path (or an object with a ``path`` such as ``ResultStream``) is read
    through :data:`_CHUNK`; text and iterables are encoded a line at a
    time.  The result is the same bytes ``read_all_lines`` would join, but
    a 475 MB plot never exists in RAM.
    """
    if isinstance(source, (bytes, str)):
        if isinstance(source, bytes):
            yield source
        elif source:
            yield source.encode("utf-8")
        return
    path = source if isinstance(source, os.PathLike) else None
    if path is None and hasattr(source, "read_all_lines"):
        path = getattr(source, "path", None)
    if path is not None:
        yield from _iter_path(path)
        return
    if hasattr(source, "read_all_lines"):
        lines: Iterable[Any] = source.read_all_lines()
    elif isinstance(source, Iterable):
        lines = source
    else:
        raise TypeError(f"cannot archive {type(source).__name__}")
    yield from _iter_lines(lines)


def _size_hint(source: Any) -> int | None:
    """The source's size when it is cheap to know (to pick a compressor)."""
    if isinstance(source, (bytes, str)):
        return len(source)
    path = source if isinstance(source, os.PathLike) else None
    if path is None and hasattr(source, "read_all_lines"):
        path = getattr(source, "path", None)
    if path is not None:
        try:
            return os.stat(path).st_size
        except OSError:
            return None
    return None


def _entry_info(name: str, compress_type: int) -> zipfile.ZipInfo:
    # A fixed timestamp makes two archives of the same content byte-equal
    # (apart from the manifest's own creation time), which keeps diffs and
    # tests honest. 1980-01-01 is ZIP's epoch.
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compress_type
    info.external_attr = 0o644 << 16
    return info


def _write_entry(zf: zipfile.ZipFile, name: str, source: Any,
                 profile: int) -> zipfile.ZipInfo:
    """Stream one entry into the open archive and return its ``ZipInfo``."""
    size = _size_hint(source)
    compress = profile if size is None or size >= _SMALL_ENTRY \
        else zipfile.ZIP_DEFLATED
    info = _entry_info(name, compress)
    # force_zip64 only when the entry may pass the 2 GiB local-header limit
    # (an unknown-size stream could): every other entry stays an ordinary
    # header that even the simplest unzipper reads.
    force64 = size is None or size > _ZIP64_LIMIT
    with zf.open(info, "w", force_zip64=force64) as dst:
        for chunk in _iter_source(source):
            dst.write(chunk)
    return info


def _normalise_results(results: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, source in (results or {}).items():
        clean = _safe_name(name)
        if clean in out:
            raise RbdzError(f"two result files are both named {clean!r}")
        out[clean] = source
    return out


def write_archive(path: str | os.PathLike, model_text: str, *,
                  results: dict[str, Any] | None = None,
                  manifest: dict[str, Any] | None = None,
                  compression: str = DEFAULT_COMPRESSION,
                  created: str | None = None,
                  generator: str | None = None) -> dict[str, Any]:
    """Write *model_text* (and optional *results*) as an `.rbdz`.

    Returns the manifest that was written.  A result source may be a
    string, bytes, a path, a ``ResultStream`` or an iterable of lines; it
    is streamed into the archive, not read into memory first.
    """
    if compression not in COMPRESSION_PROFILES:
        raise ValueError(f"unknown compression {compression!r}; choose from "
                         f"{', '.join(sorted(COMPRESSION_PROFILES))}")
    profile = COMPRESSION_PROFILES[compression]
    result_sources = _normalise_results(results)
    model_bytes = model_text.encode("utf-8")
    if not model_bytes.strip():
        raise RbdzError("the model is empty")
    generator = generator or (manifest or {}).get("generator")
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
        "bytes": len(model_bytes),
        "sha256": hashlib.sha256(model_bytes).hexdigest(),
    }

    result_entries: dict[str, dict[str, Any]] = {}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A unique temp name means two saves in the same window/folder cannot
    # empty each other's half-written archive. Unlike `mkstemp`, an ordinary
    # create keeps the file mode a normal save gets (0666 minus the umask),
    # so a shared archive is not silently owner-only.
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            _write_entry(zf, MODEL_NAME, model_bytes, profile)
            for name in sorted(result_sources):
                info = _write_entry(zf, RESULTS_DIR + name,
                                    result_sources[name], profile)
                result_entries[name] = {"bytes": info.file_size}
            if result_entries:
                doc["results"] = {**(doc.get("results") or {}),
                                  "files": result_entries}
            doc["files"] = {
                MODEL_NAME: {"bytes": len(model_bytes)},
                **{RESULTS_DIR + k: v for k, v in result_entries.items()},
                MANIFEST_NAME: {},
            }
            # The manifest goes last: it names the entries and their sizes, so
            # it can only be complete once they have been written.
            zf.writestr(
                _entry_info(MANIFEST_NAME, zipfile.ZIP_DEFLATED),
                json.dumps(doc, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return doc


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def _entries(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Map every readable entry's safe name to its ``ZipInfo``.

    Reading by ``ZipInfo`` (not by the normalised name) matters: an archive
    written elsewhere may spell an entry ``./results/x`` or use backslashes,
    and its stored name is not the name this module normalises to.
    """
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise RbdzError(
            f"the archive has {len(infos):,} entries; the limit is "
            f"{MAX_ENTRIES:,}")
    entries: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.is_dir():
            continue
        name = _safe_name(info.filename)
        if name in entries:
            raise RbdzError(f"the archive has two entries named {name!r}")
        entries[name] = info
    return entries


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, *,
                what: str) -> bytes:
    """Read one entry, turning a damaged/unsupported stream into `RbdzError`."""
    try:
        return zf.read(info)
    except _READ_ERRORS as exc:
        raise RbdzError(
            f"{what} could not be read from the archive ({exc})") from exc


def _check_manifest(manifest: dict[str, Any]) -> None:
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


def _read_manifest(zf: zipfile.ZipFile,
                   entries: dict[str, zipfile.ZipInfo]) -> dict[str, Any]:
    """The validated manifest, or ``{}`` for a container that has none."""
    info = entries.get(MANIFEST_NAME)
    if info is None:
        return {}
    if info.file_size > MAX_MANIFEST_BYTES:
        raise RbdzError(
            f"the archive manifest is {info.file_size:,} bytes; the limit "
            f"is {MAX_MANIFEST_BYTES:,}")
    raw = _read_entry(zf, info, what=MANIFEST_NAME)
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RbdzError("the archive manifest is not valid JSON") from exc
    if not isinstance(loaded, dict):
        raise RbdzError("the archive manifest is not a JSON object")
    _check_manifest(loaded)
    return loaded


def _verify_model(manifest: dict[str, Any], raw: bytes) -> None:
    """Check the model bytes against the manifest's hash, when it has one."""
    model = manifest.get("model")
    expected = model.get("sha256") if isinstance(model, dict) else None
    if not isinstance(expected, str) or not expected:
        return
    if hashlib.sha256(raw).hexdigest() != expected.lower():
        raise RbdzError(
            "model.rbds does not match the archive manifest; the archive "
            "was changed after it was written")


def _check_unpacked(entries: Iterable[zipfile.ZipInfo],
                    limit: int) -> None:
    total = sum(info.file_size for info in entries)
    if total > limit:
        raise RbdzError(
            f"the archive would unpack to {total / (1024 ** 3):.1f} GB; "
            f"the limit is {limit / (1024 ** 3):.1f} GB")


def read_archive(path: str | os.PathLike, *, with_results: bool = True,
                 max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES
                 ) -> RbdzArchive:
    """Read an archive's model (always) and result files (when requested)."""
    results: dict[str, str] = {}
    try:
        with zipfile.ZipFile(path, "r") as zf:
            entries = _entries(zf)
            if MODEL_NAME not in entries:
                raise RbdzError(
                    f"not a RAPTOR Studio archive: no {MODEL_NAME}")
            # Only the entries this call reads count against the size cap:
            # a model-only open must not be refused because the archive
            # also carries a legitimate large plot.
            wanted = entries if with_results else {
                name: entries[name]
                for name in (MODEL_NAME, MANIFEST_NAME) if name in entries}
            _check_unpacked(wanted.values(), max_uncompressed_bytes)
            manifest = _read_manifest(zf, entries)
            model_raw = _read_entry(zf, entries[MODEL_NAME], what=MODEL_NAME)
            _verify_model(manifest, model_raw)
            try:
                model_text = model_raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise RbdzError(f"{MODEL_NAME} is not UTF-8 text") from exc
            if with_results:
                for name in sorted(entries):
                    if not name.startswith(RESULTS_DIR):
                        continue
                    key = name[len(RESULTS_DIR):]
                    if not key:
                        continue
                    raw = _read_entry(zf, entries[name],
                                      what=f"result file {key!r}")
                    try:
                        results[key] = raw.decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise RbdzError(
                            f"result file {key!r} is not UTF-8 text") from exc
    except zipfile.BadZipFile as exc:
        raise RbdzError(f"{path} is not a ZIP archive") from exc
    return RbdzArchive(path=str(path), model_text=model_text,
                       manifest=manifest, results=results,
                       names=list(entries))


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
        shutil.copyfile(self.path, path)


def extract_results(path: str | os.PathLike, destination: str | os.PathLike,
                    *,
                    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES
                    ) -> tuple[dict[str, Path], dict[str, Any]]:
    """Extract the result entries into *destination* and return their paths.

    Returns ``({name: path}, manifest)``.  Extraction is per entry and
    streams each one through a fixed buffer.  Unsafe names are refused, like
    ``read_archive``.  The caller owns the destination (the GUI gives it a
    ``TemporaryDirectory``).
    """
    out: dict[str, Path] = {}
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(path, "r") as zf:
            entries = _entries(zf)
            _check_unpacked(entries.values(), max_uncompressed_bytes)
            manifest = _read_manifest(zf, entries)
            for name in sorted(entries):
                if not name.startswith(RESULTS_DIR):
                    continue
                key = name[len(RESULTS_DIR):]
                if not key:
                    continue
                # `key` is already a safe relative path; write it under the
                # destination without letting `..` or a drive letter escape.
                dest = target / key
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Only the archive side of the copy is wrapped: a full disk
                # must raise its own OSError, not "the archive is damaged".
                with zf.open(entries[name]) as src, open(dest, "wb") as dst:
                    while True:
                        try:
                            chunk = src.read(_CHUNK)
                        except _READ_ERRORS as exc:
                            raise RbdzError(
                                f"result file {key!r} could not be read "
                                f"from the archive ({exc})") from exc
                        if not chunk:
                            break
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

"""Disk-backed result streams — the GUI's opt-in "Files & Reports"
sinks (mirroring Raptor 7's design: plot and log files are
written only when requested, and written incrementally to disk rather
than accumulated in RAM, so a long simulation cannot exhaust memory).

A `ResultStream` behaves like the `list[str]` the engine's sinks expect
(`append`/`extend`/`__len__`/`__iter__`), so one can be passed straight
through as `ao_sink=`/`capacity_sink=`/`cost_sink=`. Rows go to a temp
file, one per line, flushed as they land; readers tail the file with the
incremental `read_new_lines()` (used by the live Graphs view) and
`read_all_lines()`.

Lifecycle: created per run batch by MainWindow; `close()` deletes the
temp file. `export_to(path)` copies it out using Raptor 7's
`rpt_*` file naming.
"""
from __future__ import annotations

import os
import shutil
import tempfile


class ResultStream:
    _HEADER_NOTE = "raptor-studio stream file"

    def __init__(self, kind: str, header: str | None = None,
                 directory: str | None = None):
        self.kind = kind          # "ao" | "capacity" | "cost" | "event_log"
        self.count = 0
        self._read_offset = 0
        self._closed = False
        fd, self.path = tempfile.mkstemp(
            prefix=f"raptor_{kind}_", suffix=".txt",
            dir=directory or None)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if header:
                f.write(header + "\n")
                # `len()` is the number of rows readers will see; the
                # header is one of them (the Engine's header-once checks
                # use truthiness, not this count).
                self.count = 1

    # -- engine-facing list interface -----------------------------------
    def append(self, row: str) -> None:
        if self._closed:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(row if row.endswith("\n") else row + "\n")
        self.count += 1

    def extend(self, rows) -> None:
        """Append many rows with one file open.

        The old implementation called `append` per row, which opened,
        wrote and closed the temp file once per line. That is fine for a
        handful of plot rows but pathological for a report that can run to
        millions of lines, so batch writers use this.
        """
        if self._closed:
            return
        rows = list(rows)
        if not rows:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(r if r.endswith("\n") else r + "\n")
        self.count += len(rows)

    def __len__(self) -> int:
        return self.count

    def __iter__(self):
        return iter(self.read_all_lines())

    def __bool__(self) -> bool:
        # Truthiness mirrors a list: an empty stream is falsy, so the
        # engine's `if not _ao_sink:` header-once checks keep working.
        return self.count > 0

    # -- reader side -----------------------------------------------------
    def read_all_lines(self) -> list[str]:
        """Every row so far (header rows included — callers already
        handle the engines' header/separator conventions).

        A closed stream reads as empty rather than raising: the Results
        dock reads plot data lazily and may still hold the previous
        batch's stream while the next batch opens its own (which deletes
        the old temp file). `_update_graphs` reads both plots on every
        refresh, so without this a live Ao update could trip over the
        stale, already-deleted capacity stream and fail the run with
        FileNotFoundError."""
        if self._closed:
            return []
        try:
            with open(self.path, encoding="utf-8",
                      errors="replace") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            # Closed concurrently (or temp file reaped externally): the
            # data is intentionally gone, so report an empty stream.
            return []

    def read_preview(self, max_lines: int, max_bytes: int) -> list[str]:
        """First `max_lines` rows, stopping at `max_bytes` of text.

        Used by the Results dock's Raw view: it must never materialise a
        multi-million-line End-of-Run report just to display a preview.
        The full stream stays on disk for Export.
        """
        if self._closed or max_lines <= 0 or max_bytes <= 0:
            return []
        out: list[str] = []
        used = 0
        try:
            with open(self.path, encoding="utf-8",
                      errors="replace") as f:
                for line in f:
                    out.append(line.rstrip("\n"))
                    used += len(line)
                    if len(out) >= max_lines or used >= max_bytes:
                        break
        except FileNotFoundError:
            return []
        return out

    def read_new_lines(self) -> list[str]:
        """Only rows appended since the last `read_new_lines()` call —
        the incremental tail read the live Graphs view uses. Reads as
        empty on a closed/deleted stream (see `read_all_lines`)."""
        if self._closed:
            return []
        try:
            with open(self.path, encoding="utf-8",
                      errors="replace") as f:
                f.seek(self._read_offset)
                chunk = f.read()
                self._read_offset = f.tell()
        except FileNotFoundError:
            return []
        return chunk.splitlines() if chunk else []

    def rewind(self) -> None:
        self._read_offset = 0

    # -- lifecycle --------------------------------------------------------
    def export_to(self, path: str) -> None:
        """Copy the stream to `path` (the 'export on demand' path —
        callers suggest Raptor 7's `rpt_*` naming)."""
        shutil.copyfile(self.path, path)

    def close(self) -> None:
        """Delete the temp file. Safe to call twice; further appends are
        dropped and reads report an empty stream (a finished batch's
        stream may outlive its window — the Results dock still holds the
        reference while the next batch opens its own)."""
        self._closed = True
        try:
            os.remove(self.path)
        except OSError:
            pass

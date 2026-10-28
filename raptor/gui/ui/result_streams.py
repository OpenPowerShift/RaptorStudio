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

    # -- engine-facing list interface -----------------------------------
    def append(self, row: str) -> None:
        if self._closed:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(row if row.endswith("\n") else row + "\n")
        self.count += 1

    def extend(self, rows) -> None:
        for r in rows:
            self.append(r)

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

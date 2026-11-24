"""Saving an `.rbdz` off the GUI thread, with a progress bar.

An archive carries the run's result files (an event log alone can be
hundreds of MB) and compressing them takes seconds to minutes.  The model
text and the result-source list are snapshotted on the GUI thread (both are
fast); the zip is then written by a worker thread while the window keeps
repainting and scrolling.  Anything that could change the written file --
edits, runs, another save, open/close -- is blocked until the worker is
done, so the file is a consistent snapshot of the moment Save was pressed.

The call itself still *returns when the save has finished* (it waits in a
local event loop), so a caller that must have the file before going on --
the close prompt's Save, Save version now, Save As then continue -- needs no
special handling.
"""
from __future__ import annotations

import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QEventLoop, QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QLabel, QProgressBar, QScrollBar, QWidget)

from raptor.gui import i18n

# A save quicker than this never shows a bar: no flicker for small files.
SHOW_AFTER_MS = 300
_EMIT_EVERY = 0.05          # seconds between progress signals

_BLOCKED = (
    QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride, QEvent.Type.Shortcut,
    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
    QEvent.Type.ContextMenu, QEvent.Type.DragEnter, QEvent.Type.DragMove,
    QEvent.Type.Drop, QEvent.Type.InputMethod,
    QEvent.Type.TabletPress, QEvent.Type.TouchBegin,
)


class WorkerJob(QThread):
    """Runs ``fn(progress)`` on a worker thread; ``progress(done, total)``
    is throttled and re-emitted as :attr:`progressed` on the GUI thread."""

    progressed = Signal(object, object)       # done bytes, total or None

    def __init__(self, fn: Callable[[Callable[[int, int | None], None]], Any],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self.result: Any = None
        self.error: BaseException | None = None
        self._last = 0.0

    def progress(self, done: int, total: int | None) -> None:
        now = time.monotonic()
        if now - self._last >= _EMIT_EVERY or (total is not None
                                               and done >= total):
            self._last = now
            self.progressed.emit(done, total)

    def run(self) -> None:                    # worker thread
        try:
            self.result = self._fn(self.progress)
        except BaseException as exc:          # noqa: BLE001 - handed to the GUI
            self.error = exc


class InputLock(QObject):
    """While a save runs: swallow the input that edits or starts things.

    Wheel, mouse movement, scroll-bar clicks and releases still pass, so
    the canvas can be scrolled and zoomed and the window repaints.  Applies
    to the owner window and the windows parented to it (floating docks).
    """

    blocked = Signal()

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self._owner = owner

    def _mine(self, obj: QObject) -> bool:
        w = obj if isinstance(obj, QWidget) else None
        while w is not None:
            if w is self._owner:
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() not in _BLOCKED or not self._mine(obj):
            return False
        if event.type() == QEvent.Type.MouseButtonPress:
            w = obj if isinstance(obj, QWidget) else None
            while w is not None:
                if isinstance(w, QScrollBar):
                    return False
                w = w.parentWidget()
        self.blocked.emit()
        return True


class SaveProgressBar(QWidget):
    """The status-bar label and bar; determinate when the total is known."""

    def __init__(self, name: str, parent: QWidget | None = None,
                 kind: str = "save") -> None:
        super().__init__(parent)
        self._kind = kind
        from PySide6.QtWidgets import QHBoxLayout
        from raptor.gui.resources.design_tokens import active_colour
        self._name = name
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 4, 0)
        self.label = QLabel(self)
        self.bar = QProgressBar(self)
        self.bar.setFixedHeight(8)
        self.bar.setFixedWidth(160)
        self.bar.setTextVisible(False)
        c = active_colour()
        self.bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 4px; "
            f"background: {c['border']}; }}"
            "QProgressBar::chunk { border-radius: 4px; "
            f"background: {c['accent']}; }}")
        self.label.setStyleSheet(
            f"QLabel {{ color: {c['accent']}; font-weight: 600; }}")
        row.addWidget(self.label)
        row.addWidget(self.bar)
        self.update_progress(0, None)

    def update_progress(self, done: int, total: int | None) -> None:
        if total:
            pct = min(100, int(done * 100 / total))
            self.bar.setRange(0, 100)
            self.bar.setValue(pct)
            self.label.setText(self._text(True).format(
                name=self._name, percent=pct))
        else:
            self.bar.setRange(0, 0)           # busy: size not known
            self.label.setText(self._text(False).format(name=self._name))

    def _text(self, known: bool) -> str:
        if self._kind == "version":
            return (i18n.tr("Saving version… {percent} %") if known
                    else i18n.tr("Saving version…"))
        return (i18n.tr("Saving {name}… {percent} %") if known
                else i18n.tr("Saving {name}…"))


class _Presenter:
    """The work's visible face: the bar (after a delay) in *host*, and a hint
    when input is held back.  *host* is the main window (status bar) or a
    dialog that offers ``add_progress_widget`` / ``remove_progress_widget``."""

    def __init__(self, host: Any, owner: QWidget, job: WorkerJob, name: str,
                 kind: str, sinks: list[Callable[[int, int | None], None]]) -> None:
        self.host, self.name, self.kind = host, name, kind
        self.bar: SaveProgressBar | None = None
        self._latest: list[Any] = [0, None]
        self._last_note = 0.0
        self.sinks = sinks
        job.progressed.connect(self._remember)
        self.job = job
        self.timer = QTimer(owner)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._show)

    def _remember(self, done: Any, total: Any) -> None:
        self._latest = [done, total]
        for sink in self.sinks:
            sink(done, total)

    def _show(self) -> None:
        bar = SaveProgressBar(self.name, kind=self.kind)
        self.bar = bar
        self.host.add_progress_widget(bar)
        bar.update_progress(*self._latest)
        self.job.progressed.connect(bar.update_progress)
        bar.show()

    def note_blocked(self) -> None:
        now = time.monotonic()
        if now - self._last_note > 2.0:
            self._last_note = now
            self.host.show_hint(
                i18n.tr("Saving — editing resumes when the save finishes"))

    def close(self) -> None:
        self.timer.stop()
        if self.bar is not None:
            self.host.remove_progress_widget(self.bar)
            self.bar.deleteLater()


class _WindowHost:
    """Status-bar placement for the main window."""

    def __init__(self, window: Any) -> None:
        self.window = window

    def __getattr__(self, name: str) -> Any:
        return getattr(self.window, name)

    def add_progress_widget(self, w: QWidget) -> None:
        self.window._statusbar.addPermanentWidget(w)

    def remove_progress_widget(self, w: QWidget) -> None:
        self.window._statusbar.removeWidget(w)

    def show_hint(self, text: str) -> None:
        self.window._statusbar.showMessage(text, 3000)


def run_blocking(owner: QWidget, host: Any, fn: Callable[..., Any], *,
                 name: str = "", kind: str = "save",
                 sinks: list[Callable[[int, int | None], None]] | None = None,
                 flag: str = "_save_in_progress") -> Any:
    """Run ``fn(progress)`` on a worker and return its result when done.

    The call waits in a local event loop, so the window keeps painting;
    :class:`InputLock` holds back editing/shortcut input to *owner* and its
    child windows meanwhile.  Raises what *fn* raised.  *flag* is set True on
    *owner* for the duration (guards elsewhere read it).
    """
    job = WorkerJob(fn, owner)
    lock = InputLock(owner)
    present = _Presenter(host, owner, job, name, kind, sinks or [])
    lock.blocked.connect(present.note_blocked)
    loop = QEventLoop(owner)
    job.finished.connect(loop.quit)
    app = QApplication.instance()
    assert app is not None
    setattr(owner, flag, True)
    app.installEventFilter(lock)
    present.timer.start(SHOW_AFTER_MS)
    try:
        job.start()
        loop.exec()
        job.wait()
    finally:
        app.removeEventFilter(lock)
        present.close()
        setattr(owner, flag, False)
        lock.deleteLater()
        job.deleteLater()
    if job.error is not None:
        raise job.error
    return job.result


def save_archive_blocking(window: Any, path: str, model_text: str,
                          sources: dict[str, Any],
                          manifest: dict[str, Any], created: str) -> None:
    """Write the archive on a worker thread and return when it is done.

    Raises whatever the write raised (so the caller's usual "Save Failed"
    handling applies).  The window stays painted and scrollable meanwhile;
    see :class:`InputLock` for what is held back.  Progress is also passed to
    ``window._save_progress_sinks`` (the History dialog shows it itself).
    """
    # Lists are shallow-copied here, on the GUI thread, so the worker reads a
    # fixed snapshot even if the dock rebinds its own lists meanwhile.
    snap = {k: (list(v) if isinstance(v, list) else v)
            for k, v in sources.items()}
    if not hasattr(window, "_save_progress_sinks"):
        window._save_progress_sinks = []

    def work(progress: Callable[[int, int | None], None]) -> None:
        from raptor.core.fileio.rbdz import write_archive
        write_archive(path, model_text, results=snap, manifest=manifest,
                      created=created, progress=progress)

    try:
        run_blocking(window, _WindowHost(window), work, name=Path(path).name,
                     sinks=getattr(window, "_save_progress_sinks", []))
    finally:
        pending_close = getattr(window, "_close_after_save", False)
        window._close_after_save = False
        if pending_close:
            QTimer.singleShot(0, window.close)

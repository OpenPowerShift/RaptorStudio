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


class ArchiveWriter(QThread):
    """Runs `write_archive` for an already-snapshotted model and sources."""

    progressed = Signal(object, object)       # done bytes, total or None

    def __init__(self, path: str, model_text: str, sources: dict[str, Any],
                 manifest: dict[str, Any], created: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._args = (path, model_text, sources, manifest, created)
        self.error: BaseException | None = None
        self._last = 0.0

    def _on_progress(self, done: int, total: int | None) -> None:
        now = time.monotonic()
        if now - self._last >= _EMIT_EVERY or (total is not None
                                               and done >= total):
            self._last = now
            self.progressed.emit(done, total)

    def run(self) -> None:                    # worker thread
        from raptor.core.fileio.rbdz import write_archive
        path, text, sources, manifest, created = self._args
        try:
            write_archive(path, text, results=sources, manifest=manifest,
                          created=created, progress=self._on_progress)
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

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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
            self.label.setText(i18n.tr("Saving {name}… {percent} %").format(
                name=self._name, percent=pct))
        else:
            self.bar.setRange(0, 0)           # busy: size not known
            self.label.setText(i18n.tr("Saving {name}…").format(
                name=self._name))


class _Presenter:
    """The save's status-bar face: the bar (after a delay) and a hint when
    input is held back."""

    def __init__(self, window: Any, job: ArchiveWriter, name: str) -> None:
        self.window, self.name = window, name
        self.bar: SaveProgressBar | None = None
        self._latest: list[Any] = [0, None]
        self._last_note = 0.0
        job.progressed.connect(self._remember)
        self.job = job
        self.timer = QTimer(window)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._show)

    def _remember(self, done: Any, total: Any) -> None:
        self._latest = [done, total]

    def _show(self) -> None:
        bar = SaveProgressBar(self.name)
        self.bar = bar
        self.window._statusbar.addPermanentWidget(bar)
        bar.update_progress(*self._latest)
        self.job.progressed.connect(bar.update_progress)
        bar.show()

    def note_blocked(self) -> None:
        now = time.monotonic()
        if now - self._last_note > 2.0:
            self._last_note = now
            self.window._statusbar.showMessage(
                i18n.tr("Saving — editing resumes when the save finishes"),
                3000)

    def close(self) -> None:
        self.timer.stop()
        if self.bar is not None:
            self.window._statusbar.removeWidget(self.bar)
            self.bar.deleteLater()


def save_archive_blocking(window: Any, path: str, model_text: str,
                          sources: dict[str, Any],
                          manifest: dict[str, Any], created: str) -> None:
    """Write the archive on a worker thread and return when it is done.

    Raises whatever the write raised (so the caller's usual "Save Failed"
    handling applies).  The window stays painted and scrollable meanwhile;
    see :class:`InputLock` for what is held back.
    """
    # Lists are shallow-copied here, on the GUI thread, so the worker reads a
    # fixed snapshot even if the dock rebinds its own lists meanwhile.
    snap = {k: (list(v) if isinstance(v, list) else v)
            for k, v in sources.items()}
    job = ArchiveWriter(path, model_text, snap, manifest, created, window)
    lock = InputLock(window)
    present = _Presenter(window, job, Path(path).name)
    lock.blocked.connect(present.note_blocked)
    loop = QEventLoop(window)
    job.finished.connect(loop.quit)
    app = QApplication.instance()
    assert app is not None

    window._save_in_progress = True
    app.installEventFilter(lock)
    present.timer.start(SHOW_AFTER_MS)
    try:
        job.start()
        loop.exec()
        job.wait()
    finally:
        app.removeEventFilter(lock)
        present.close()
        window._save_in_progress = False
        lock.deleteLater()
        job.deleteLater()
        pending_close = getattr(window, "_close_after_save", False)
        window._close_after_save = False
    if pending_close:
        QTimer.singleShot(0, window.close)
    if job.error is not None:
        raise job.error

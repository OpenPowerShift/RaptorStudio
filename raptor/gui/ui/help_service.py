"""Process-level ownership of the one in-app Help window.

RAPTOR Studio opens one model per window (see `raptor.gui.open_models`).
Help belongs to the process, not to whichever window happened to invoke it:
closing one model window must leave Help open while another remains, and the
next Help action in another window must reuse the same viewer (and its
history) rather than build a second WebEngine window.

`HelpService` is that owner. It creates the viewer lazily through an
injectable factory, remembers which window is the current owner, and routes
the viewer's callbacks (an ``opsmodel://`` link, "Switch to App") through
that owner. The service is deliberately separate from `help_viewer`: it
imports no QtWebEngine, so lifecycle tests can drive it with a fake viewer
and it can be imported while a window is closing without spinning up
WebEngine.
"""
from __future__ import annotations

import weakref
from collections.abc import Callable
from typing import Any

#: Factory callable: (open_model=..., switch_to_app=...) -> viewer.
ViewerFactory = Callable[..., Any]

#: The process's single service (created on the first Help action).
_SERVICE: HelpService | None = None


def _is_live(widget: Any) -> bool:
    """True when a Qt widget's C++ object still exists.

    A window's Python wrapper can outlive its C++ object briefly during
    shutdown; calling into it then is a hard Qt abort. Non-Qt test doubles
    have no shiboken wrapper, so they pass.
    """
    if widget is None:
        return False
    try:
        from shiboken6 import isValid
    except ImportError:                      # pragma: no cover - PySide always ships it
        return True
    try:
        return bool(isValid(widget))
    except TypeError:
        # A plain Python test double: no C++ wrapper to invalidate.
        return True
    except RuntimeError:
        return False


class HelpService:
    """Own the single `HelpViewer` and route it to the current window.

    Call `show(owner, name, anchor)` for every Help action: it makes
    `owner` current, creates the viewer on first use, and shows the
    document. `owner_closing(owner)` is called after a window leaves the
    process-wide registry; it transfers the viewer to another live window
    or closes it when none remain.
    """

    def __init__(self, viewer_factory: ViewerFactory | None = None) -> None:
        self._viewer: Any | None = None
        self._owner_ref: weakref.ref | None = None
        self._viewer_factory = viewer_factory
        self._theme_connected = False
        self._quit_hook_installed = False

    # ------------------------------------------------------------ inspector
    @property
    def viewer(self) -> Any | None:
        """The viewer, or None before the first Help action / after close."""
        return self._viewer

    @property
    def owner(self) -> Any | None:
        """The window Help currently belongs to, if it is still alive."""
        if self._viewer is not None:
            owner = getattr(self._viewer, "owner", None)
            if _is_live(owner):
                return owner
        ref = self._owner_ref
        owner = ref() if ref is not None else None
        return owner if _is_live(owner) else None

    def set_owner(self, owner: Any | None) -> None:
        """Make `owner` the window Help callbacks act on.

        Called on every Help action/focus. The reference is weak: a window
        closing must not be kept alive by Help.
        """
        self._owner_ref = weakref.ref(owner) if owner is not None else None
        if self._viewer is not None:
            self._viewer.set_owner(owner)

    # ------------------------------------------------------------ lifecycle
    def show(self, owner: Any, name: str, anchor: str | None = None) -> Any:
        """Show `name` in the shared viewer, owned by `owner` now."""
        viewer = self._ensure_viewer()
        self.set_owner(owner)
        viewer.show_doc(name, anchor)
        return viewer

    def _ensure_viewer(self) -> Any:
        if self._viewer is not None:
            return self._viewer
        factory = self._viewer_factory
        if factory is None:
            from raptor.gui.ui.help_viewer import HelpViewer
            factory = HelpViewer
        viewer = factory(open_model=self._open_model,
                         switch_to_app=self._switch_to_app)
        self._viewer = viewer
        owner = self.owner
        if owner is not None:
            viewer.set_owner(owner)
        self._connect_theme(viewer)
        self._install_quit_hook()
        return viewer

    def _install_quit_hook(self) -> None:
        """Close Help when the app quits, including embedders that never
        call `app.main()` (and the `app.exec()`-raises path). Idempotent:
        connecting twice would only call the same idempotent `close()`."""
        if self._quit_hook_installed:
            return
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
        except ImportError:                  # pragma: no cover - non-GUI tests
            return
        if app is None:
            return
        app.aboutToQuit.connect(self.close)
        self._quit_hook_installed = True

    def _connect_theme(self, viewer: Any) -> None:
        """Apply the current theme and follow later toggles (once per viewer).

        The callback is a bound method: ThemeManager stores it weakly, so a
        closed viewer's registration prunes itself.
        """
        if self._theme_connected or not hasattr(viewer, "refresh_theme"):
            return
        try:
            from raptor.gui.resources.design_tokens import ThemeManager
            manager = ThemeManager.instance()
        except Exception:                    # pragma: no cover - non-GUI tests
            return
        try:
            manager.on_changed(viewer.refresh_theme)
        except TypeError:
            # A test double's callback need not be a bound method.
            pass
        viewer.refresh_theme(manager.is_dark)
        self._theme_connected = True

    def owner_closing(self, owner: Any) -> None:
        """A window has closed. Keep Help for the others, or close it.

        Call this *after* `open_models.unregister(owner)`: `windows()` then
        lists the windows that really remain.
        """
        current = self.owner
        if current is not None and current is not owner:
            # Some other window owns Help and is still alive: leave it be.
            return
        from raptor.gui import open_models
        remaining = [w for w in open_models.windows() if w is not owner]
        if remaining:
            self.set_owner(remaining[-1])
        else:
            self.close()

    def close(self) -> None:
        """Stop WebEngine work and destroy the viewer.

        Called for the last window, `_quit_all` and aboutToQuit. `close()`
        alone only hides a QDialog; the WebEngine page (and the process-wide
        URL-scheme handler) used to keep the closed viewer alive, which is
        exactly the shutdown shape that segfaulted in #75. Drop the owner,
        stop the page, then delete the widget and drain `DeferredDelete`
        while a QApplication still exists.
        """
        viewer, self._viewer = self._viewer, None
        self._owner_ref = None
        self._theme_connected = False
        if viewer is None:
            return
        try:
            shutdown = getattr(viewer, "shutdown", None)
            if callable(shutdown):
                shutdown()
            else:
                viewer.close()
                delete = getattr(viewer, "deleteLater", None)
                if callable(delete):
                    delete()
        except RuntimeError:                 # C++ side already gone at shutdown
            pass
        self._drain_deferred_deletes()

    @staticmethod
    def _drain_deferred_deletes() -> None:
        """Run queued `deleteLater()`s now, while the app object exists."""
        try:
            from PySide6.QtCore import QCoreApplication, QEvent
        except ImportError:                  # pragma: no cover - non-GUI tests
            return
        if QCoreApplication.instance() is None:
            return
        try:
            QCoreApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete)
        except RuntimeError:                 # pragma: no cover - shutting down
            pass

    # ------------------------------------------------------------ callbacks
    def _routing_owner(self) -> Any | None:
        owner = self.owner
        if owner is not None:
            return owner
        from raptor.gui import open_models
        live = open_models.windows()
        return live[-1] if live else None

    def _open_model(self, path: str) -> None:
        owner = self._routing_owner()
        if owner is not None:
            owner.open_model_from_help(path)

    def _switch_to_app(self) -> None:
        owner = self._routing_owner()
        if owner is not None:
            owner._switch_from_help()


def route_ops_model(path: str) -> None:
    """Process-wide router for ``opsmodel://`` link requests.

    The `QWebEngineUrlSchemeHandler` must hold a callback that survives
    every viewer (and never points at one): this module function delegates
    to the current Help service, or to the last live window when Help has
    not been opened through the service.
    """
    service = _SERVICE
    if service is not None:
        service._open_model(path)
        return
    from raptor.gui import open_models
    live = open_models.windows()
    if live:
        live[-1].open_model_from_help(path)


def get_help_service() -> HelpService:
    """The process-wide service (created lazily)."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = HelpService()
    return _SERVICE


def notify_window_closed(window: Any) -> None:
    """Let the existing Help service react to a window closing.

    Deliberately does not create the service: a process that never opened
    Help must not import QtWebEngine just because a window closed.
    """
    if _SERVICE is not None:
        _SERVICE.owner_closing(window)


def note_window_activated(window: Any) -> None:
    """Make `window` the Help owner when it becomes the active model.

    So Help follows the window the user is working in, not only the one
    that first opened it. Does nothing until Help exists.
    """
    if _SERVICE is not None and _SERVICE.viewer is not None:
        _SERVICE.set_owner(window)


def close_help_viewer() -> None:
    """Close Help without creating a service if none exists."""
    if _SERVICE is not None:
        _SERVICE.close()


def reset_help_service() -> None:
    """Drop the process singleton (tests): close its viewer first."""
    global _SERVICE
    service, _SERVICE = _SERVICE, None
    if service is not None:
        service.close()

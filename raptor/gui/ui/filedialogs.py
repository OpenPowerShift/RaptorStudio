"""Theme-governed file dialogs.

The static `QFileDialog.getOpenFileName/getSaveFileName` helpers use the
platform-native dialog, which ignores the application stylesheet and
palette, so its buttons render with the desktop theme rather than our
tokens. These wrappers force Qt's non-native dialog, which the
QFileDialog, QTreeView/QListView, QToolButton and QPushButton rules in
design_tokens.py already theme, so every file dialog follows the active
theme. All file-ask call sites go through here.

The dialogs also use a plain icon provider. The desktop's own provider asks the MIME database for an icon per
file type; for an AsciiDoc file that is an icon called `asciidoctor`, which a theme without it turns into a path
relative to the working directory and the console fills with
`qt.svg: Cannot open file '<cwd>/asciidoctor.svg'`. A file, a folder and a drive need no lookup. Every wrapper
here sets it; an application-wide event filter also sets it on any QFileDialog that Qt or a future call site
creates directly, so one missed constructor cannot bring the warning back.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QFileInfo, QObject, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFileDialog, QFileIconProvider,
                               QToolButton)

from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import active_colour


class PlainIconProvider(QFileIconProvider):
    """Folder / drive / file icons by kind only; never a per-file-type (MIME) lookup."""

    def icon(self, arg):                                    # noqa: A003 - Qt's name
        if isinstance(arg, QFileInfo):
            kind = (QFileIconProvider.IconType.Folder if arg.isDir()
                    else QFileIconProvider.IconType.Drive if arg.isRoot()
                    else QFileIconProvider.IconType.File)
            return super().icon(kind)
        return super().icon(arg)


_PROVIDER = PlainIconProvider()


def plain_icons(dlg: QFileDialog) -> QFileDialog:
    """Give `dlg` the plain icon provider (and return it)."""
    dlg.setIconProvider(_PROVIDER)
    return dlg


class _PlainIconFilter(QObject):
    """Apply the plain provider to every QFileDialog just before Qt populates
    it. The wrappers in this module already do so; this covers dialogs Qt
    creates itself (for example a web view's save/open chooser) and any
    call site that constructs QFileDialog directly."""

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 - Qt's name
        if isinstance(obj, QFileDialog) and event.type() in (
                QEvent.Type.Show, QEvent.Type.Polish):
            plain_icons(obj)
        return False


_FILTER: _PlainIconFilter | None = None


def install_plain_icons(app: QApplication | None = None) -> None:
    """Install the dialog-wide safety net once per QApplication."""
    global _FILTER
    app = app or QApplication.instance()
    if app is None or (_FILTER is not None and _FILTER.parent() is app):
        return
    _FILTER = _PlainIconFilter(app)
    app.installEventFilter(_FILTER)


def theme_file_dialog(dlg: QFileDialog) -> QFileDialog:
    """Theme every stock toolbar icon in a non-native file dialog.

    Qt's `SP_FileDialog*` pixmaps are platform bitmaps, so the folder/list/
    detail glyphs (and the nav arrows) do not follow the active palette --
    they stayed white on the light theme, and near-invisible on dark. Paint
    them from our own theme-aware icon set instead. All dialog entry points
    come through here, so a fix reaches Open/Save, Import, Compare and the
    image-export chooser alike.
    """
    plain_icons(dlg)
    text = active_colour()["text"]
    by_name = {
        "backButton": icons.back_arrow_icon(text),
        "forwardButton": icons.forward_arrow_icon(text),
        "toParentButton": icons.up_arrow_icon(text),
        "newFolderButton": icons.new_folder_icon(text),
        "listModeButton": icons.list_view_icon(text),
        "detailModeButton": icons.detail_view_icon(text),
    }
    for tb in dlg.findChildren(QToolButton):
        tb.setIconSize(QSize(18, 18))
        tb.setMinimumSize(28, 28)
        icon = by_name.get(tb.objectName())
        if icon is not None:
            tb.setIcon(icon)
    return dlg


def _dialog(parent, caption: str, directory: str, file_filter: str, mode) -> QFileDialog:
    dlg = QFileDialog(parent, caption, directory, file_filter)
    dlg.setAcceptMode(mode)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    return theme_file_dialog(dlg)


def getSaveFileName(parent, caption: str, directory: str = "",
                    file_filter: str = ""):
    """The themed non-native save dialog. Returns `(path, selected_filter)` like the static helper
    (`("", "")` when cancelled)."""
    dlg = _dialog(parent, caption, directory, file_filter, QFileDialog.AcceptMode.AcceptSave)
    if not dlg.exec() or not dlg.selectedFiles():
        return "", ""
    return dlg.selectedFiles()[0], dlg.selectedNameFilter()


def getOpenFileName(parent, caption: str, directory: str = "",
                    file_filter: str = ""):
    """The themed non-native open dialog. Returns `(path, selected_filter)` like the static helper."""
    dlg = _dialog(parent, caption, directory, file_filter, QFileDialog.AcceptMode.AcceptOpen)
    if not dlg.exec() or not dlg.selectedFiles():
        return "", ""
    return dlg.selectedFiles()[0], dlg.selectedNameFilter()


def getExistingDirectory(parent, caption: str, directory: str = "") -> str:
    """The themed non-native folder chooser ("" when cancelled)."""
    dlg = _dialog(parent, caption, directory, "", QFileDialog.AcceptMode.AcceptOpen)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    if not dlg.exec() or not dlg.selectedFiles():
        return ""
    return dlg.selectedFiles()[0]


__all__ = [
    "PlainIconProvider",
    "QIcon",
    "getExistingDirectory",
    "getOpenFileName",
    "getSaveFileName",
    "install_plain_icons",
    "plain_icons",
    "theme_file_dialog",
]

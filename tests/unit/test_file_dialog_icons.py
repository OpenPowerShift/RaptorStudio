"""File dialogs use folder/file icons by kind, never the desktop's per-file-type (MIME) lookup, which is what
logged `qt.svg: Cannot open file '<cwd>/asciidoctor.svg'` for a directory holding AsciiDoc files."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QFileInfo
from PySide6.QtWidgets import QApplication, QFileDialog, QFileIconProvider

from raptor.gui.ui import filedialogs

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_the_provider_never_asks_for_a_file_type_icon(qapp, monkeypatch):
    asked = []
    real = QFileIconProvider.icon

    def spy(self, arg):
        asked.append(type(arg).__name__)
        return real(self, arg)
    monkeypatch.setattr(QFileIconProvider, "icon", spy)
    provider = filedialogs.PlainIconProvider()
    provider.icon(QFileInfo("/nonexistent/manual.adoc"))
    provider.icon(QFileInfo("/"))
    provider.icon(QFileIconProvider.IconType.Folder)
    assert "QFileInfo" not in asked                  # only IconType requests reach Qt


def test_the_helpers_build_dialogs_with_the_plain_provider(qapp):
    dlg = filedialogs.plain_icons(QFileDialog())
    assert isinstance(dlg.iconProvider(), filedialogs.PlainIconProvider)


def test_even_unwrapped_dialogs_get_the_plain_provider_via_the_app_filter(qapp):
    """Qt creates some dialogs itself (and a future call site may forget);
    the application filter must catch those too."""
    filedialogs.install_plain_icons(qapp)
    dlg = QFileDialog()
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dlg.show()
    qapp.processEvents()
    try:
        assert isinstance(dlg.iconProvider(), filedialogs.PlainIconProvider)
    finally:
        dlg.close()


def test_stock_toolbar_glyphs_are_replaced_with_theme_icons(qapp):
    """Create New Folder / List View / Detail View are Qt platform pixmaps;
    they stayed white because only the nav arrows were repainted."""
    from PySide6.QtWidgets import QToolButton

    dlg = filedialogs.theme_file_dialog(QFileDialog())
    wanted = {"newFolderButton", "listModeButton", "detailModeButton"}
    buttons = {tb.objectName(): tb for tb in dlg.findChildren(QToolButton)
               if tb.objectName() in wanted}
    assert set(buttons) == wanted
    for name, tb in buttons.items():
        assert not tb.icon().isNull(), name
        assert not tb.icon().pixmap(18, 18).isNull(), name

"""The full-page File view, opened by the File button left of the ribbon
tabs (Office calls it the backstage).

It replaces the ribbon's old File tab, whose Recent dropdown held six
files. Here the recent list is long (`MainWindow._RECENT_MAX`), searchable
by name or folder, and shows where each file lives and when it was last
changed. The file commands are the same QActions the ribbon and the Quick
Access strip use, so their availability is the registry's, not a copy.

Esc, the back arrow, or running any command closes it.
"""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QPushButton, QStackedWidget, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from raptor.gui.command_table import BACKSTAGE, BACKSTAGE_ACTIONS
from raptor.gui.resources import icons
from raptor.gui.resources.design_tokens import TYPE, active_colour

if TYPE_CHECKING:
    from raptor.gui.ui.main_window import MainWindow

#: Recent-list columns.
NAME, FOLDER, MODIFIED = 0, 1, 2
#: An example card's picture, px (the thumbnails are 448 x 252, drawn at
#: half size for a high-DPI screen).
CARD_W, CARD_H = 224, 126
#: How many examples and recent files the Home page shows; the New and
#: Recent pages show them all.
HOME_EXAMPLES, HOME_RECENT = 3, 6
NEW_COLUMNS = 4
#: Setting: open the File view's Home page at start-up.
START_KEY = "startup/show_start_page"


def _when(path: str) -> str:
    """A file's modification time, short and local."""
    try:
        t = os.path.getmtime(path)
    except OSError:
        return ""
    now = time.time()
    if now - t < 86400 and time.localtime(t).tm_yday == time.localtime(now).tm_yday:
        return time.strftime("Today %H:%M", time.localtime(t))
    return time.strftime("%d %b %Y", time.localtime(t))


def _short(folder: str) -> str:
    """A folder with the home directory written as ~."""
    home = os.path.expanduser("~")
    if folder == home or folder.startswith(home + os.sep):
        return "~" + folder[len(home):]
    return folder


class Backstage(QWidget):
    """Covers the whole window while open, ribbon included."""

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self._win = window
        self.setObjectName("Backstage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- navigation column ------------------------------------------
        nav = QFrame()
        nav.setObjectName("BackstageNav")
        nav.setFixedWidth(210)
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(0, 12, 0, 12)
        nl.setSpacing(2)
        self._back = QToolButton()
        self._back.setObjectName("BackstageBack")
        self._back.setToolTip("Back to the model (Esc)")
        self._back.setAccessibleName("Close the File view")
        self._back.setIconSize(QSize(24, 24))
        self._back.clicked.connect(self.close_view)
        nl.addWidget(self._back)
        nl.addSpacing(10)

        self._pages = QStackedWidget()
        self._nav_buttons: list[QPushButton] = []
        self._page_by_name: dict[str, tuple[QWidget, QPushButton]] = {}
        self._cards: list[QPushButton] = []
        #: Card grids that reflow with the width (see `_reflow`).
        self._grids: list[tuple[QGridLayout, list[QPushButton]]] = []
        self._home_page = self._build_home_page()
        self._new_page = self._build_new_page()
        self._recent_page = self._build_recent_page()
        self._share_page = self._build_share_page()
        self._add_page_button(nl, "Home", self._home_page, "home_icon")
        self._add_page_button(nl, "New", self._new_page, "new_file_icon")
        self._add_page_button(nl, "Recent", self._recent_page,
                              "recent_files_icon")
        for cid in BACKSTAGE[0][1]:
            if cid != "file.new":       # the New page's Blank model card
                nl.addWidget(self._nav_command(cid))
        self._add_page_button(nl, "Print and Export", self._share_page,
                              "printer_icon")
        for cid in BACKSTAGE_ACTIONS:
            nl.addWidget(self._nav_command(cid))
        nl.addStretch(1)
        for cid in ("view.prefs", "help.about"):
            nl.addWidget(self._nav_command(cid))

        root.addWidget(nav)
        root.addWidget(self._pages, 1)

        # Esc closes this view through the window's own Esc shortcut
        # (`MainWindow._handle_escape_activated`): a second Esc shortcut
        # here made the key ambiguous, so neither fired.
        window.installEventFilter(self)

    # ---- construction ---------------------------------------------------

    def _add_page_button(self, layout, text: str, page: QWidget,
                         icon_fn: str) -> None:
        btn = QPushButton(text)
        btn.setObjectName("BackstageNavItem")
        btn.setCheckable(True)
        btn.setProperty("navIcon", icon_fn)
        btn.setIconSize(QSize(18, 18))
        btn.clicked.connect(lambda _c=False, p=page, b=btn: self._go(p, b))
        self._pages.addWidget(page)
        self._nav_buttons.append(btn)
        self._page_by_name[text] = (page, btn)
        layout.addWidget(btn)

    def _nav_command(self, cid: str) -> QPushButton:
        """A nav-column button that runs a command (closing the view first,
        so its dialog opens over the model, not over this page)."""
        action = self._win._commands.action(cid)
        btn = QPushButton(action.text())
        btn.setObjectName("BackstageNavItem")
        btn.setToolTip(action.toolTip())
        btn.setIconSize(QSize(18, 18))
        btn.setProperty("navIcon",
                        self._win._commands.specs[cid].icon.partition(":")[0])
        btn.setEnabled(action.isEnabled())
        action.changed.connect(
            lambda b=btn, a=action: b.setEnabled(a.isEnabled()))
        btn.clicked.connect(lambda _c=False, a=action: self._run(a))
        return btn

    # ---- Home and New ---------------------------------------------------

    def _scroll_page(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        """A page whose body scrolls (a short window must not clip the
        gallery)."""
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setObjectName("BackstagePage")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("BackstagePage")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(40, 28, 40, 28)
        lay.setSpacing(12)
        head = QLabel(title)
        head.setObjectName("BackstageTitle")
        lay.addWidget(head)
        scroll.setWidget(body)
        return scroll, lay

    def _section(self, lay: QVBoxLayout, text: str,
                 more: tuple[str, str] | None = None) -> None:
        """A section heading, with an optional link to the full page."""
        row = QHBoxLayout()
        label = QLabel(text)
        label.setObjectName("BackstageSection")
        row.addWidget(label)
        row.addStretch(1)
        if more:
            link = QPushButton(more[0])
            link.setObjectName("BackstageLink")
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.clicked.connect(lambda _c=False, n=more[1]: self.go_to(n))
            row.addWidget(link)
        lay.addSpacing(8)
        lay.addLayout(row)

    def _card(self, title: str, idea: str, on_click, example=None) -> QPushButton:
        """A gallery card: a picture of the model, its name, what it
        teaches. `example` None is the Blank model card."""
        card = QPushButton()
        card.setObjectName("BackstageCard")
        card.setFixedSize(CARD_W + 2, CARD_H + 66)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setAccessibleName(title)
        card.setToolTip(f"{title}: {idea}")
        col = QVBoxLayout(card)
        col.setContentsMargins(1, 1, 1, 8)
        col.setSpacing(2)
        pic = QLabel()
        pic.setObjectName("BackstageCardPicture")
        pic.setFixedSize(CARD_W, CARD_H)
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(pic)
        name = QLabel(title)
        name.setObjectName("BackstageCardName")
        sub = QLabel(idea)
        sub.setObjectName("BackstageCardText")
        sub.setWordWrap(True)
        for w in (name, sub):
            w.setContentsMargins(10, 0, 10, 0)
            col.addWidget(w)
        col.addStretch(1)
        for w in (pic, name, sub):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        card.setProperty("example", example)
        card.clicked.connect(on_click)
        self._cards.append(card)
        return card

    def _blank_card(self) -> QPushButton:
        new = self._win._commands.action("file.new")
        card = self._card("Blank model", "A Start, an End and one block",
                          lambda: self._run(new))
        new.changed.connect(lambda c=card, a=new: c.setEnabled(a.isEnabled()))
        return card

    def _example_card(self, ex) -> QPushButton:
        return self._card(ex.title, ex.idea,
                          lambda _c=False, e=ex: self._open_example(e), ex)

    def _open_example(self, ex) -> None:
        self.close_view()
        self._win._open_example(ex.file)

    def _build_home_page(self) -> QWidget:
        from raptor.gui.examples import EXAMPLES
        page, lay = self._scroll_page("Start")

        self._section(lay, "New", ("More examples", "New"))
        # A first model to learn from, and the two richest to explore.
        basics = [e for e in EXAMPLES if not e.advanced]
        advanced = [e for e in EXAMPLES if e.advanced]
        lay.addLayout(self._card_grid(
            [self._blank_card()] + [self._example_card(ex) for ex in
                                    (basics[:1] + advanced)[:HOME_EXAMPLES]]))

        self._section(lay, "Recent", ("All recent files", "Recent"))
        self._home_recent = QVBoxLayout()
        self._home_recent.setSpacing(0)
        lay.addLayout(self._home_recent)

        # Help, one click from the first screen: a new user's real question
        # is "what do I do now?", and the User Guide's first chapter is the
        # answer.
        self._section(lay, "Learn")
        learn = QHBoxLayout()
        learn.setSpacing(10)
        for cid in ("help.guide", "help.manual", "help.shortcuts"):
            action = self._win._commands.action(cid)
            btn = QPushButton(action.text())
            btn.setObjectName("BackstageLearn")
            btn.setProperty("learnIcon",
                            self._win._commands.specs[cid].icon.partition(":")[0])
            btn.setIconSize(QSize(24, 24))
            btn.setToolTip(action.toolTip())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, a=action: self._run(a))
            learn.addWidget(btn)
        learn.addStretch(1)
        lay.addLayout(learn)

        from PySide6.QtWidgets import QCheckBox
        from raptor.gui.ui import settings
        self._show_at_start = QCheckBox("Show this page when RAPTOR Studio starts")
        self._show_at_start.setObjectName("BackstageNote")
        self._show_at_start.setChecked(settings.get_bool(START_KEY, True))
        self._show_at_start.toggled.connect(
            lambda on: settings.set_value(START_KEY, on))
        lay.addSpacing(16)
        lay.addWidget(self._show_at_start)
        lay.addStretch(1)
        return page

    def _build_new_page(self) -> QWidget:
        from raptor.gui.examples import EXAMPLES, TUTORIALS
        page, lay = self._scroll_page("New")
        note = QLabel("Start from a blank model or an example. An example "
                      "opens as a new, unsaved copy: Save As keeps your "
                      "changes.")
        note.setObjectName("BackstageNote")
        note.setWordWrap(True)
        lay.addWidget(note)
        basics = [self._blank_card()] + [self._example_card(e)
                                         for e in EXAMPLES if not e.advanced]
        self._section(lay, "Basics: one idea each, from the User Guide")
        lay.addLayout(self._card_grid(basics))
        self._section(lay, "Advanced: realistic systems, several features at once")
        lay.addLayout(self._card_grid(
            [self._example_card(e) for e in EXAMPLES if e.advanced]))
        self._section(
            lay, "Tutorial Examples: the User Guide's other exercises")
        lay.addLayout(self._card_grid(
            [self._example_card(e) for e in TUTORIALS]))
        lay.addStretch(1)
        return page

    def _card_grid(self, cards: list[QPushButton]) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        self._grids.append((grid, cards))
        self._place(grid, cards, NEW_COLUMNS)
        return grid

    @staticmethod
    def _place(grid: QGridLayout, cards: list[QPushButton], cols: int) -> None:
        for card in cards:
            grid.removeWidget(card)
        for c in range(grid.columnCount()):
            grid.setColumnStretch(c, 0)
        for n, card in enumerate(cards):
            grid.addWidget(card, n // cols, n % cols)
        grid.setColumnStretch(cols, 1)

    def _reflow(self) -> None:
        """As many cards to a row as the page has room for: a fixed four
        (or the Home page's single row) needed sideways scrolling on a
        1024-pixel screen."""
        room = self._pages.width() - 80 - 24          # page margins, scrollbar
        cols = max(1, min(NEW_COLUMNS + 1, (room + 14) // (CARD_W + 2 + 14)))
        for grid, cards in self._grids:
            if grid.property("cols") != cols:
                grid.setProperty("cols", cols)
                self._place(grid, cards, cols)

    def _fill_home_recent(self) -> None:
        while self._home_recent.count():
            w = self._home_recent.takeAt(0).widget()
            if w is not None:
                # Hidden now: deletion waits for the event loop, and until
                # then the old row still painted over the page below.
                w.hide()
                w.deleteLater()
        paths = self._win._recent_list()[:HOME_RECENT]
        if not paths:
            empty = QLabel("Files you open or save will appear here.")
            empty.setObjectName("BackstageNote")
            self._home_recent.addWidget(empty)
            return
        c = active_colour()
        for path in paths:
            folder = _short(os.path.dirname(path))
            if len(folder) > 60:                  # middle-elided, no scroll
                folder = folder[:28] + "…" + folder[-30:]
            btn = QPushButton(f"{os.path.basename(path)}     {folder}")
            btn.setMinimumWidth(0)
            btn.setObjectName("BackstageRecentRow")
            btn.setIcon(icons.document_icon(c["accent"]))
            btn.setIconSize(QSize(18, 18))
            btn.setToolTip(path)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, p=path: self._open_path(p))
            self._home_recent.addWidget(btn)

    def _open_path(self, path: str) -> None:
        self.close_view()
        self._win._open_path(path)

    def _build_recent_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("BackstagePage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 28, 40, 28)
        lay.setSpacing(12)
        title = QLabel("Recent")
        title.setObjectName("BackstageTitle")
        lay.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._search = QLineEdit()
        self._search.setObjectName("BackstageSearch")
        self._search.setPlaceholderText("Search recent files by name or folder")
        self._search.setClearButtonEnabled(True)
        self._search.setAccessibleName("Search recent files")
        self._search.textChanged.connect(self._filter)
        self._search.returnPressed.connect(self._open_first_match)
        self._search.installEventFilter(self)
        row.addWidget(self._search, 1)
        self._browse = QPushButton("Browse…")
        self._browse.setToolTip("Open a file that is not in the list (Ctrl+O)")
        self._browse.clicked.connect(
            lambda: self._run(self._win._commands.action("file.open")))
        row.addWidget(self._browse)
        lay.addLayout(row)

        self._list = QTreeWidget()
        self._list.setObjectName("BackstageRecent")
        self._list.setHeaderLabels(["Name", "Folder", "Modified"])
        self._list.setRootIsDecorated(False)
        self._list.setUniformRowHeights(True)
        self._list.setAlternatingRowColors(False)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        self._list.itemActivated.connect(self._open_item)
        self._list.setAccessibleName("Recent files")
        header = self._list.header()
        header.setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(FOLDER, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(MODIFIED, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self._list, 1)

        self._empty = QLabel()
        self._empty.setObjectName("BackstageNote")
        self._empty.setWordWrap(True)
        lay.addWidget(self._empty)
        return page

    def _build_share_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("BackstagePage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 28, 40, 28)
        lay.setSpacing(16)
        title = QLabel("Print and Export")
        title.setObjectName("BackstageTitle")
        lay.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self._tiles: list[QPushButton] = []
        for n, cid in enumerate(BACKSTAGE[1][1]):
            tile = self._tile(self._win._commands.action(cid))
            grid.addWidget(tile, n // 2, n % 2)
            self._tiles.append(tile)
        lay.addLayout(grid)
        lay.addStretch(1)
        return page

    def _tile(self, action: QAction) -> QPushButton:
        """A large button: the command's icon, its name, and what it does
        (from its tooltip, minus any shortcut or "Unavailable" suffix),
        wrapped rather than cut off."""
        tile = QPushButton()
        tile.setObjectName("BackstageTile")
        tile.setMinimumSize(360, 72)
        tile.setToolTip(action.toolTip())
        tile.setAccessibleName(action.text())
        row = QHBoxLayout(tile)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(14)
        icon = QLabel()
        icon.setObjectName("BackstageTileIcon")
        icon.setFixedSize(32, 32)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        name = QLabel(action.text().rstrip("…"))
        name.setObjectName("BackstageTileName")
        desc = QLabel(action.toolTip().split("\n")[0].split(" (")[0])
        desc.setObjectName("BackstageTileText")
        desc.setWordWrap(True)
        words.addWidget(name)
        words.addWidget(desc)
        words.addStretch(1)
        row.addLayout(words, 1)
        for label in (icon, name, desc):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        tile.setProperty("tileAction", action)

        def sync(a=action, t=tile, d=desc) -> None:
            # The File view is built once, before the first model opens;
            # following the action here also refreshes the reason ("open
            # or create a model first") once that no longer applies.
            t.setEnabled(a.isEnabled())
            t.setToolTip(a.toolTip())
            d.setText(a.toolTip().split("\n")[0].split(" (")[0])

        action.changed.connect(sync)
        sync()
        tile.clicked.connect(lambda _c=False, a=action: self._run(a))
        return tile

    # ---- behaviour ------------------------------------------------------

    def open_view(self, page: str = "Home") -> None:
        """Show the view on `page` ("Home", "New", "Recent", "Print and
        Export"). Home is the start screen: new, recent and help at once."""
        self._restyle()
        self.setGeometry(self._win.rect())
        self.reload()
        self._search.clear()
        self.go_to(page)
        self.show()
        self.raise_()
        self._reflow()
        if page == "Recent":
            self._search.setFocus()

    def go_to(self, name: str) -> None:
        page, btn = self._page_by_name[name]
        self._go(page, btn)

    def close_view(self) -> None:
        self.hide()

    def _go(self, page: QWidget, button: QPushButton) -> None:
        self._pages.setCurrentWidget(page)
        for b in self._nav_buttons:
            b.setChecked(b is button)

    def _run(self, action: QAction) -> None:
        self.close_view()
        if action.isEnabled():
            action.trigger()

    def reload(self) -> None:
        """Rebuild the recent lists from settings."""
        self._fill_home_recent()
        self._list.clear()
        for path in self._win._recent_list():
            item = QTreeWidgetItem([os.path.basename(path),
                                    _short(os.path.dirname(path)), _when(path)])
            item.setData(NAME, Qt.ItemDataRole.UserRole, path)
            item.setToolTip(NAME, path)
            item.setToolTip(FOLDER, path)
            item.setIcon(NAME, icons.document_icon(active_colour()["accent"]))
            self._list.addTopLevelItem(item)
        self._filter(self._search.text())

    def sync_state(self) -> None:
        """Called on every command refresh; only the recent list is not an
        action the registry already keeps current."""
        if self.isVisible():
            self.reload()

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        shown = 0
        for i in range(self._list.topLevelItemCount()):
            item = self._list.topLevelItem(i)
            path = str(item.data(NAME, Qt.ItemDataRole.UserRole)).lower()
            hit = all(w in path for w in words)
            item.setHidden(not hit)
            shown += hit
        first = next((self._list.topLevelItem(i)
                      for i in range(self._list.topLevelItemCount())
                      if not self._list.topLevelItem(i).isHidden()), None)
        self._list.setCurrentItem(first)
        total = self._list.topLevelItemCount()
        if not total:
            self._empty.setText(
                "No recent files yet. Files you open or save appear here; "
                "use Browse… to open one.")
        elif not shown:
            self._empty.setText(f"No recent file matches “{text}”.")
        else:
            self._empty.setText("")
        self._empty.setVisible(bool(self._empty.text()))

    def visible_paths(self) -> list[str]:
        return [self._list.topLevelItem(i).data(NAME, Qt.ItemDataRole.UserRole)
                for i in range(self._list.topLevelItemCount())
                if not self._list.topLevelItem(i).isHidden()]

    def _open_first_match(self) -> None:
        item = self._list.currentItem()
        if item is not None and not item.isHidden():
            self._open_item(item)

    def _open_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        path = item.data(NAME, Qt.ItemDataRole.UserRole)
        self.close_view()
        if path:
            self._win._open_path(path)

    def _context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        path = item.data(NAME, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self._open_item(item))
        menu.addAction("Remove from list", lambda: self._forget(path))
        menu.addSeparator()
        menu.addAction("Clear the whole list", self._clear_all)
        menu.popup(self._list.viewport().mapToGlobal(pos))

    def _forget(self, path: str) -> None:
        self._win._forget_recent(path)
        self.reload()

    def _clear_all(self) -> None:
        self._win._recent_clear()
        self.reload()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._search and event.type() == QEvent.Type.KeyPress \
                and event.key() == Qt.Key.Key_Down:
            # Down from the search box walks into the matches.
            first = next((self._list.topLevelItem(i)
                          for i in range(self._list.topLevelItemCount())
                          if not self._list.topLevelItem(i).isHidden()), None)
            if first is not None:
                self._list.setFocus()
                self._list.setCurrentItem(first)
            return True
        if obj is self._win and event.type() == QEvent.Type.Resize \
                and self.isVisible():
            self.setGeometry(self._win.rect())
        return False

    def resizeEvent(self, event) -> None:                 # noqa: N802
        super().resizeEvent(event)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self, self._reflow)    # after layout; dies with self

    def _restyle(self) -> None:
        c = active_colour()
        self._back.setIcon(icons.back_arrow_icon("#FFFFFF"))
        # The nav column is navy in both themes, so its glyphs are white.
        for btn in self.findChildren(QPushButton, "BackstageNavItem"):
            btn.setIcon(getattr(icons, btn.property("navIcon"))("#FFFFFF"))
        from raptor.gui.examples import thumbnail
        from raptor.gui.resources.design_tokens import ThemeManager
        dark = ThemeManager.instance().is_dark
        for card in self._cards:
            pic = card.findChild(QLabel, "BackstageCardPicture")
            ex = card.property("example")
            if ex is not None:
                from PySide6.QtGui import QPixmap
                pm = QPixmap(str(thumbnail(ex, dark)))
                pm.setDevicePixelRatio(2.0)
                pic.setPixmap(pm)
            else:
                pic.setPixmap(icons.new_file_icon(c["accent"]).pixmap(56, 56))
        for btn in self.findChildren(QPushButton, "BackstageLearn"):
            btn.setIcon(getattr(icons, btn.property("learnIcon"))(c["accent"]))
        for tile in self._tiles:
            pm = tile.property("tileAction").icon().pixmap(32, 32)
            tile.findChild(QLabel, "BackstageTileIcon").setPixmap(pm)
        self.setStyleSheet(f"""
            #Backstage {{ background: {c['surface']}; }}
            #BackstagePage {{ background: {c['surface']}; }}
            #BackstageNav {{ background: {c['nav']}; border: none; }}
            #BackstageBack {{
                background: transparent; border: none;
                margin-left: 14px; padding: 6px; border-radius: 18px;
            }}
            #BackstageBack:hover {{ background: rgba(255, 255, 255, 0.12); }}
            #BackstageNavItem {{
                color: #FFFFFF; background: transparent; border: none;
                border-left: 4px solid transparent;
                border-radius: 0; text-align: left;
                font-family: {TYPE['family']}; font-size: {TYPE['body'] + 1}px;
                padding: 9px 18px; min-height: 22px;
            }}
            #BackstageNavItem:hover {{ background: rgba(255, 255, 255, 0.10); }}
            #BackstageNavItem:checked {{
                background: rgba(255, 255, 255, 0.16);
                border-left: 4px solid {c['accent']};
                font-weight: 600;
            }}
            #BackstageNavItem:disabled {{ color: rgba(255, 255, 255, 0.40); }}
            #BackstageTitle {{
                font-family: {TYPE['family']}; font-size: 26px;
                font-weight: 300; color: {c['text']};
            }}
            #BackstageSearch {{
                font-size: {TYPE['body']}px; padding: 6px 10px;
                min-height: 22px;
            }}
            #BackstageRecent {{
                font-size: {TYPE['body']}px; border: 1px solid {c['border']};
            }}
            #BackstageRecent::item {{ padding: 6px 4px; }}
            #BackstageNote {{ color: {c['muted']}; font-size: {TYPE['body']}px; }}
            #BackstageTile {{
                border: 1px solid {c['border']}; border-radius: 6px;
                background: {c['surface']};
            }}
            #BackstageTileName {{
                font-size: {TYPE['body'] + 1}px; font-weight: 600;
                color: {c['text']};
            }}
            #BackstageTileText {{
                font-size: {TYPE['label']}px; color: {c['muted']};
            }}
            #BackstageTile:disabled #BackstageTileName {{ color: {c['muted']}; }}
            #BackstageSection {{
                font-size: {TYPE['body'] + 3}px; font-weight: 600;
                color: {c['text']};
            }}
            #BackstageLink, #BackstageRecentRow, #BackstageLearn {{
                background: transparent; border: none; text-align: left;
                font-size: {TYPE['body']}px;
            }}
            #BackstageLink {{ color: {c['accent']}; }}
            #BackstageLink:hover {{ text-decoration: underline; }}
            #BackstageRecentRow {{
                color: {c['text']}; padding: 7px 8px; border-radius: 4px;
            }}
            #BackstageRecentRow:hover, #BackstageLearn:hover {{
                background: {c['canvas']};
            }}
            #BackstageLearn {{
                color: {c['text']}; padding: 8px 14px 8px 10px;
                border: 1px solid {c['border']}; border-radius: 6px;
            }}
            #BackstageCard {{
                border: 1px solid {c['border']}; border-radius: 6px;
                background: {c['surface']}; padding: 0;
            }}
            #BackstageCard:hover {{ border-color: {c['accent']}; }}
            #BackstageCardPicture {{
                background: {c['canvas']}; border: none;
                border-top-left-radius: 5px; border-top-right-radius: 5px;
            }}
            #BackstageCardName {{
                font-size: {TYPE['body']}px; font-weight: 600;
                color: {c['text']}; padding-top: 6px;
            }}
            #BackstageCardText {{
                font-size: {TYPE['label']}px; color: {c['muted']};
            }}
            #BackstageTile:hover {{
                border-color: {c['accent']}; background: {c['canvas']};
            }}
        """)

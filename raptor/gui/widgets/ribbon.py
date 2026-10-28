"""Ribbon widgets for RAPTOR Studio.

Built on QWidget/QAction — no proprietary ribbon dependency. Actions are
the single command source for menus, shortcuts, and ribbon controls.

The anatomy is Office's:

* a group is a run of COLUMNS: a large button (32 px icon over its label)
  fills a column at full height; small buttons (16 px icon beside the
  label) and input controls stack three to a column;
* the group's title sits on one baseline along the bottom, and groups are
  divided by a hairline rather than boxed;
* a page that does not fit its window shrinks ONE STEP AT A TIME, the
  lowest-priority group first: large buttons become small (all but the
  group's primary one), then small buttons drop their labels, and only
  then does a group fold into a single button. Nothing is ever hidden,
  and widening the window walks the same steps back.
"""
from __future__ import annotations

from raptor.gui.ui.qtcompat import req
from dataclasses import dataclass
from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QTabBar, QToolButton,
    QVBoxLayout, QWidget,
)

from raptor.gui.resources.design_tokens import active_colour, RIBBON, SPACING, TYPE

#: Sizes a ribbon control can take. "large": icon over label, full
#: height. "small": icon beside label, one of three in a column. "icon":
#: small without its label (the tooltip still names it).
LARGE, SMALL, ICON_ONLY = "large", "small", "icon"
#: How many small controls stack in one column.
STACK = 3
#: A group's priority unless the layout table says otherwise.
_DEFAULT_PRIORITY = 5


def stack_height() -> int:
    """Height of a full column: three small rows and the gaps between."""
    return STACK * RIBBON["small_height"] + (STACK - 1) * RIBBON["row_gap"]


def wrap_label(text: str) -> str:
    """A large button's label on two lines, Office style ("Add\\nHierarchy"),
    split at the space nearest the middle. Short or one-word labels stay on
    one line."""
    if " " not in text or len(text) <= 9:
        return text
    mid = len(text) / 2
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + "\n" + text[cut + 1:]


@dataclass(frozen=True)
class RibbonActionSpec:
    action_id: str
    large: bool = False
    show_text: bool = True


class RibbonButton(QToolButton):
    """One command on the ribbon, in any of the three sizes.

    The size can change at runtime (`set_mode`) as the page shrinks and
    grows, so everything that depends on it -- style, icon size, label
    wrapping, the `ribbonLarge` property the stylesheet keys on -- is set
    in one place."""

    def __init__(self, action: QAction, large: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonButton")
        self.setDefaultAction(action)
        self.setAutoRaise(True)
        if action.menu() is not None:
            self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.preferred_large = large
        self._mode = ""
        self.setAccessibleName(action.text())
        self.setProperty("ribbonActionText", action.text())
        self.setProperty("ribbonKeyTip", action.property("keyTip") or "")
        self.set_mode(LARGE if large else SMALL)

    def mode(self) -> str:
        return self._mode

    def reapply_mode(self) -> None:
        """Re-assert the current size after something changed the style or
        label behind its back (the key-tip overlay)."""
        mode, self._mode = self._mode, ""
        self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        if mode == LARGE:
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            self.setIconSize(QSize(RIBBON["icon_large"], RIBBON["icon_large"]))
            self.setMinimumWidth(RIBBON["large_action_width"])
            self.setFixedHeight(stack_height())
        else:
            self.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonIconOnly if mode == ICON_ONLY
                else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.setIconSize(QSize(RIBBON["icon_normal"], RIBBON["icon_normal"]))
            self.setMinimumWidth(0)
            self.setFixedHeight(RIBBON["small_height"])
        self.setProperty("ribbonLarge", mode == LARGE)
        self._apply_text()
        # The stylesheet keys on `ribbonLarge`; a property change is only
        # seen after a re-polish.
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()

    def _apply_text(self) -> None:
        action = self.defaultAction()
        if action is None:
            return
        text = action.text()
        self.setText(wrap_label(text) if self._mode == LARGE else text)

    def actionEvent(self, event) -> None:
        super().actionEvent(event)
        # QToolButton copies the action's text back on every change (a
        # toggle that renames itself, "Dark Theme" / "Light Theme"), which
        # would undo the two-line wrap.
        if event.type() == QEvent.Type.ActionChanged \
                and event.action() is self.defaultAction():
            self._apply_text()
            self.setProperty("ribbonActionText", event.action().text())


def _item_width(widget: QWidget) -> int:
    return max(widget.sizeHint().width(), widget.minimumWidth())


class RibbonGroup(QFrame):
    """One titled run of columns on a ribbon page.

    Items are kept as a declarative list of `(widget, preferred_size)` and
    placed into columns by `_build_columns`, so a change of size level is a
    rebuild from the same list, never an accumulation."""

    #: Higher = stays at full size longer when the page is too narrow. Set
    #: from the layout table (`Group.priority`).
    DEFAULT_PRIORITY = _DEFAULT_PRIORITY

    #: Size levels a group steps through as its page narrows. Folding the
    #: whole group into one button is the step after the last, and is the
    #: page's job (`CollapsedGroup`).
    FULL, REDUCED, ICONS = 0, 1, 2

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("RibbonGroup")
        self.collapse_priority = self.DEFAULT_PRIORITY
        self._collapsed_button: CollapsedGroup | None = None
        self._items: list[tuple[QWidget, str]] = []
        self._level = self.FULL
        self._width_cache: dict[int, int] = {}
        self._body = QHBoxLayout()
        self._body.setContentsMargins(4, 3, 4, 0)
        self._body.setSpacing(RIBBON["column_gap"])
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter
                           | Qt.AlignmentFlag.AlignBottom)
        label.setObjectName("RibbonGroupTitle")
        label.setFixedHeight(RIBBON["group_title_height"])
        self._label = label
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 2)
        outer.setSpacing(0)
        outer.addLayout(self._body)
        outer.addStretch(1)
        outer.addWidget(label)

    # -- contents ------------------------------------------------------

    def add_action(self, action: QAction, *, large: bool = False) -> RibbonButton:
        button = RibbonButton(action, large, self)
        self._add(button, LARGE if large else SMALL)
        return button

    def add_widget(self, widget: QWidget, *, large: bool = False) -> None:
        """A control that is not a plain command button: a combo, a labelled
        spin box, a menu button. It takes one small slot unless `large`."""
        if isinstance(widget, RibbonButton):
            widget.preferred_large = large
        self._add(widget, LARGE if large else SMALL)

    def _add(self, widget: QWidget, size: str) -> None:
        widget.setParent(self)
        self._items.append((widget, size))
        self._width_cache.clear()
        self._build_columns()

    def items(self) -> list[tuple[QWidget, str]]:
        return list(self._items)

    # -- size levels ---------------------------------------------------

    def _sizes_at(self, level: int) -> list[str]:
        """Each item's size at `level`.

        REDUCED turns every large item small except the group's first --
        its primary command keeps the big target (Run, Add Block). ICONS
        then drops the labels of the small BUTTONS; input controls keep
        theirs, since a bare combo or number box says nothing."""
        sizes = []
        primary_seen = False
        for _widget, size in self._items:
            if size == LARGE:
                keep = level == self.FULL or not primary_seen
                primary_seen = True
                sizes.append(LARGE if keep else SMALL)
            else:
                sizes.append(SMALL)
        if level >= self.ICONS:
            sizes = [ICON_ONLY if s == SMALL and isinstance(w, RibbonButton)
                     else s for s, (w, _) in zip(sizes, self._items)]
        return sizes

    def level_count(self) -> int:
        """How many size levels this group has (folding not included)."""
        return self.ICONS + 1

    def level(self) -> int:
        return self._level

    def set_level(self, level: int) -> None:
        if level == self._level and self._body.count():
            return
        self._level = level
        self._build_columns()

    def width_at(self, level: int) -> int:
        """The width this group needs at `level`, measured by laying it out
        at that level (then restoring it). Cached: labels and icon sizes do
        not change between relayouts, only on a style change."""
        if level not in self._width_cache:
            current = self._level
            self._level = level
            self._apply_modes()
            self._width_cache[level] = self._measure()
            self._level = current
            self._apply_modes()
        return self._width_cache[level]

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._width_cache.clear()
        super().changeEvent(event)

    def _apply_modes(self) -> None:
        for (widget, _), size in zip(self._items, self._sizes_at(self._level)):
            if isinstance(widget, RibbonButton):
                widget.set_mode(size)

    def _columns(self) -> list[list[QWidget]]:
        """Which items share a column at the current level. Pure: a function
        of the item list and the level, so it is testable without a screen.

        A large item is a column of its own; consecutive small items fill
        columns of up to `STACK`, and a large item ends the column before
        it. Hidden items take no slot."""
        cols: list[list[QWidget]] = []
        run: list[QWidget] = []
        for (widget, _), size in zip(self._items, self._sizes_at(self._level)):
            if widget.isHidden() and widget.testAttribute(
                    Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
                continue
            if size == LARGE:
                if run:
                    cols.append(run)
                    run = []
                cols.append([widget])
            else:
                run.append(widget)
                if len(run) == STACK:
                    cols.append(run)
                    run = []
        if run:
            cols.append(run)
        return cols

    def _measure(self) -> int:
        m = self._body.contentsMargins()
        cols = self._columns()
        width = sum(max(_item_width(w) for w in col) for col in cols)
        width += self._body.spacing() * max(0, len(cols) - 1)
        width += m.left() + m.right()
        return max(width, self._label.sizeHint().width() + 8)

    def _build_columns(self) -> None:
        self._apply_modes()
        while self._body.count():
            item = self._body.takeAt(0)
            col = item.layout() if item is not None else None
            if col is not None:
                while col.count():
                    col.takeAt(0)
        for col_items in self._columns():
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(RIBBON["row_gap"])
            for w in col_items:
                col.addWidget(w, 0, Qt.AlignmentFlag.AlignLeft)
            col.addStretch(1)
            self._body.addLayout(col)
        self._body.invalidate()
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        """Width from the current columns' own items, not the layout's cache,
        which goes stale once the stylesheet and icons land after
        construction (it once handed a group 262 px for content needing
        441, and Qt elided the labels)."""
        base = super().sizeHint()
        return QSize(self._measure(), base.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(self._measure(), super().minimumSizeHint().height())


class CollapsedGroup(QToolButton):
    """A whole group folded into one button, the last step for a page too
    narrow to show it.

    The popup holds the REAL group widget, not a copy: its buttons, combos,
    spin boxes and their state are the same objects, so nothing is
    duplicated, nothing can drift, and widening the window simply puts the
    group back inline. A command that lives in a collapsed group is one
    click further away, never gone."""

    #: Names shown in the tooltip, so the button says what is inside.
    _TIP_NAMES = 8

    def __init__(self, group: RibbonGroup, parent=None):
        super().__init__(parent)
        self.ribbon_group = group
        title = group._label.text()
        self.setObjectName("RibbonCollapsedGroup")
        # "&" would be read as a mnemonic marker: "Grid & Focus" showed as
        # "Grid_Focus".
        self.setText(wrap_label(title).replace("&", "&&") + " ▾")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(RIBBON["icon_large"], RIBBON["icon_large"]))
        self.setAutoRaise(True)
        self.setFixedHeight(stack_height())
        self.setMinimumWidth(RIBBON["large_action_width"])
        self._icon_key = 0
        self.setAccessibleName(f"{title} commands")
        self.setAccessibleDescription(
            f"Opens the {title} group of ribbon commands")
        self._popup = QFrame(self, Qt.WindowType.Popup)
        self._popup.setObjectName("RibbonGroupPopup")
        self._popup_layout = QVBoxLayout(self._popup)
        self._popup_layout.setContentsMargins(6, 6, 6, 6)
        self._popup_layout.addWidget(group)
        self.clicked.connect(self.show_popup)
        # A plain command closes the popup, as in every ribbon; a control
        # that opens its own menu, or takes input, leaves it open.
        for btn in group.findChildren(RibbonButton):
            action = btn.defaultAction()
            if action is not None and btn.menu() is None:
                action.triggered.connect(self._popup.hide)
        self._refresh_tooltip()
        self.refresh_icon()

    def _pick_icon(self) -> QIcon:
        """The group's first command that has an icon, so the button means
        something (Annotate shows the Text tool); else a generic glyph."""
        for widget, _size in self.ribbon_group.items():  # noqa: PERF102 -- a list, not a dict
            if isinstance(widget, QToolButton) and not widget.icon().isNull():
                return widget.icon()
        from raptor.gui.resources import icons
        return icons.more_icon(active_colour()["text"])

    def refresh_icon(self) -> None:
        icon = self._pick_icon()
        if icon.cacheKey() != self._icon_key:
            self._icon_key = icon.cacheKey()
            self.setIcon(icon)

    def paintEvent(self, event) -> None:
        # The icons are theme-painted and replaced wholesale on a theme
        # change, so a copy taken at build time would go stale. Checking at
        # paint time follows the change without registering a callback
        # that outlives the button.
        self.refresh_icon()
        super().paintEvent(event)

    def _refresh_tooltip(self) -> None:
        names = [b.defaultAction().text() for b in
                 self.ribbon_group.findChildren(RibbonButton)
                 if b.defaultAction() is not None and b.defaultAction().text()]
        shown = ", ".join(names[:self._TIP_NAMES])
        more = len(names) - self._TIP_NAMES
        if more > 0:
            shown += f" and {more} more"
        self.setToolTip(f"{self.ribbon_group._label.text()}: {shown}"
                        if shown else self.ribbon_group._label.text())

    def adopt_group(self) -> None:
        """Put the group back into the popup (after it was shown inline)."""
        self._popup_layout.addWidget(self.ribbon_group)

    def show_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()
            return
        self._popup.adjustSize()
        pos = self.mapToGlobal(QPoint(0, self.height()))
        screen = self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            pos.setX(max(area.left(),
                         min(pos.x(), area.right() - self._popup.width())))
        self._popup.move(pos)
        self._popup.show()


def plan_fit(widths: list[list[int]], priorities: list[int], available: int,
             collapsed_width: int, gap: int = 0) -> list[int]:
    """Each group's size level so the page fits `available` px.

    `widths[i][k]` is group i's width at level k; level `len(widths[i])`
    means folded into a button `collapsed_width` wide. Pure, so it is
    testable without a screen.

    Shrinks one step at a time, the group with the lowest EFFECTIVE level
    first: its size level, less half a level per point of priority below
    the default. So groups of equal priority shrink evenly (none squeezed
    to icons while its neighbour stays large), and a low-priority group
    runs a level or two ahead -- Hierarchy folds before Build loses its
    labels. Ties go to the rightmost group (the far end of a page is where
    the least-used groups sit).
    A step goes to the group's next level that is actually NARROWER: a
    level can be wider than the one before (a large button turned small
    puts its whole label on one line), and stopping there would leave the
    group at full size while its neighbours were squeezed to icons.
    """
    n = len(widths)
    levels = [0] * n

    def width_at(i: int, level: int) -> int:
        return collapsed_width if level >= len(widths[i]) else widths[i][level]

    def total() -> int:
        return (sum(width_at(i, levels[i]) for i in range(n))
                + gap * max(n - 1, 0))

    while available > 0 and total() > available:
        steps = []
        for i in range(n):
            now = width_at(i, levels[i])
            nxt = next((k for k in range(levels[i] + 1, len(widths[i]) + 1)
                        if width_at(i, k) < now), None)
            if nxt is not None:
                effective = levels[i] - (_DEFAULT_PRIORITY - priorities[i]) / 2
                steps.append((effective, priorities[i], -i, nxt))
        if not steps:
            break
        _lvl, _prio, neg_i, nxt = min(steps)
        levels[-neg_i] = nxt
    return levels


def plan_collapse(widths: list[int], priorities: list[int], available: int,
                  collapsed_width: int, gap: int = 6) -> set[int]:
    """Which groups fold, for groups that have one size only. Kept as the
    simple case of `plan_fit`."""
    levels = plan_fit([[w] for w in widths], priorities, available,
                      collapsed_width, gap)
    return {i for i, lv in enumerate(levels) if lv >= 1}


class RibbonPage(QWidget):
    """One ribbon tab's worth of groups on ONE row, left-aligned (as in
    every ribbon: the eye starts at the left edge, and a stretched page
    would scatter a group's commands). Re-fits on resize via `plan_fit`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._groups: list[RibbonGroup] = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 0, 4, 0)
        outer.setSpacing(0)
        self._row = outer
        outer.addStretch(1)

    def add_group(self, group: RibbonGroup) -> None:
        self._groups.append(group)
        self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        for g in self._groups:
            # Unpolished widgets report widths ~15 % too large, which would
            # shrink groups on a page that actually fits.
            g.ensurePolished()
        widths = [[g.width_at(k) for k in range(g.level_count())]
                  for g in self._groups]
        while self._row.count() > 1:   # keep the trailing stretch
            item = self._row.takeAt(0)
            w = req(item).widget()
            if w is not None:
                w.setParent(None)
        if not self._groups:
            return
        m = self._row.contentsMargins()
        available = self.width() - m.left() - m.right()
        priorities = [g.collapse_priority for g in self._groups]
        levels = plan_fit(widths, priorities, available,
                          self._collapsed_width())
        for i, g in enumerate(self._groups):
            if levels[i] >= g.level_count():
                button = g._collapsed_button
                if button is None:
                    button = g._collapsed_button = CollapsedGroup(g, self)
                else:
                    button.adopt_group()
                g.set_level(g.level_count() - 1)
                button._refresh_tooltip()
                self._row.insertWidget(self._row.count() - 1, button)
            else:
                g.set_level(levels[i])
                self._row.insertWidget(self._row.count() - 1, g)
                g.show()

    def _collapsed_width(self) -> int:
        """Width a folded group takes: measured from a real button once one
        exists, else a fair estimate."""
        for g in self._groups:
            if g._collapsed_button is not None:
                return g._collapsed_button.sizeHint().width()
        return 84

    def collapsed_groups(self) -> list[RibbonGroup]:
        """The groups currently folded into buttons."""
        return [g for g in self._groups
                if g._collapsed_button is not None
                and g._collapsed_button.parent() is not None
                and g.parent() is g._collapsed_button._popup]

    def needed_width(self) -> int:
        """Width the page takes at its current levels."""
        m = self._row.contentsMargins()
        total = m.left() + m.right()
        for g in self._groups:
            if g in self.collapsed_groups():
                total += req(g._collapsed_button).sizeHint().width()
            else:
                total += g.sizeHint().width()
        return total

    def content_height(self) -> int:
        """Height the tallest group needs. Every page must fit
        `RIBBON["content_height"]`; the ribbon tests enforce it."""
        h = 0
        for g in self._groups:
            g.ensurePolished()
            h = max(h, g.sizeHint().height())
        return h


class Ribbon(QWidget):
    """Tabs above the current page, with the File button and the Quick
    Access strip on the tab row."""

    #: The File button was pressed: open the full-page File view.
    file_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # A plain QWidget paints a stylesheet background only with this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        # QTabBar's sizeHint() is hysteretic: once scroll buttons or elided
        # text kick in even briefly during layout, it shrinks to match and
        # stays there. There is always room in this row, so tell it never
        # to elide or scroll.
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tab_bar.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        # Manual show/hide host, NOT QStackedWidget: a QStackedLayout sizes
        # itself to the widest page even while it is hidden.
        self._pages: list[RibbonPage] = []
        self._page_host = QWidget()
        self._page_host_layout = QVBoxLayout(self._page_host)
        self._page_host_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_bar.currentChanged.connect(self._show_page)

        # File: not a tab but a button that opens the full-page File view,
        # as in Office. Accent-filled so it reads as the way in to it.
        self.file_button = QToolButton()
        self.file_button.setObjectName("RibbonFileButton")
        self.file_button.setText("File")
        self.file_button.setToolTip(
            "Open, save, print, export and recent files (Alt, then E)")
        self.file_button.setAccessibleName("File")
        self.file_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_button.clicked.connect(self.file_requested)

        # Quick Access strip (Office convention): small icon-only buttons at
        # the LEFT of the tab row, visible on every tab. At the right end of
        # a wide window it sat ~1,700 px from the tabs.
        self._qat = QWidget()
        self._qat.setObjectName("RibbonQat")
        self._qat_layout = QHBoxLayout(self._qat)
        self._qat_layout.setContentsMargins(4, 0, 6, 0)
        self._qat_layout.setSpacing(1)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(0)
        tab_row.addWidget(self._qat)
        tab_row.addWidget(self.file_button)
        tab_row.addWidget(self.tab_bar)
        tab_row.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(tab_row)
        root.addWidget(self._page_host)
        self._max_content_height = RIBBON["content_height"]
        self.setFixedHeight(RIBBON["tab_height"] + self._max_content_height)
        self._apply_theme(active_colour())

    def apply_content_height(self) -> None:
        """The ribbon is ONE height on every tab, at every width: it used to
        resize to the page, so the canvas and both docks jumped on every tab
        switch."""
        want = RIBBON["tab_height"] + self._max_content_height
        if want != self.height():
            self.setFixedHeight(want)

    def _current_page(self) -> RibbonPage | None:
        idx = self.tab_bar.currentIndex()
        if 0 <= idx < len(self._pages):
            return self._pages[idx]
        return self._pages[0] if self._pages else None

    def set_quick_access_actions(self, actions: list[QAction]) -> None:
        """(Re)populate the Quick Access strip. Callers pass the actions once
        their icons and shortcuts are set (Undo/Redo come from the undo
        stack, later than the ribbon)."""
        while self._qat_layout.count():
            item = self._qat_layout.takeAt(0)
            w = req(item).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for action in actions:
            btn = QToolButton(self._qat)
            btn.setObjectName("RibbonQatButton")
            btn.setDefaultAction(action)
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setIconSize(QSize(RIBBON["icon_normal"], RIBBON["icon_normal"]))
            self._qat_layout.addWidget(btn)

    def _show_page(self, index: int) -> None:
        for i, p in enumerate(self._pages):
            p.setVisible(i == index)
        self.apply_content_height()

    def _apply_theme(self, c: dict):
        self.tab_bar.setStyleSheet(f"""
            QTabBar::tab {{
                font-family: {TYPE['family']};
                font-size: {TYPE['body']}px;
                padding: {SPACING['xs']}px {SPACING['md']}px;
                border: none;
                border-bottom: 2px solid transparent;
                color: {c['muted']};
                background: transparent;
            }}
            QTabBar::tab:selected {{
                color: {c['accent']};
                border-bottom: 2px solid {c['accent']};
                font-weight: 600;
            }}
            QTabBar::tab:hover:!selected {{
                color: {c['text']};
                background: {c['canvas']};
            }}
        """)
        # Scoped to the ribbon itself. Unscoped, these two rules cascaded
        # onto EVERY child: each button drew a hairline under itself, and
        # the File button's accent fill was painted over.
        self.setStyleSheet(
            f"Ribbon {{ background: {c['surface']};"
            f" border-bottom: 1px solid {c['border']}; }}")

    def _refresh_theme(self, c: dict):
        self._apply_theme(c)

    def add_page(self, title: str, page: RibbonPage) -> int:
        index = len(self._pages)
        page.setVisible(index == 0)
        self._pages.append(page)
        self._page_host_layout.addWidget(page)
        self.tab_bar.addTab(title)
        return index

    def select_page(self, title: str) -> None:
        for index in range(self.tab_bar.count()):
            if self.tab_bar.tabText(index) == title:
                self.tab_bar.setCurrentIndex(index)
                return
        raise KeyError(title)

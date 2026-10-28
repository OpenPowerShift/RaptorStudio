"""Layout tests for the ribbon widgets.

`ribbon.py` had no unit tests at all, which is why its reflow was
carried by hand-maintained running state (`_row_width`, mutated as a side
effect of `add_action`) that could not be recomputed once items were
added. The refactor to a declarative item list exists so a group's
contents can change at runtime; these tests are what make that safe.

They assert on `_reflow_plan()` -- a pure function of the item list --
rather than on pixel geometry, which is unstable across Qt versions and
themes. Where a size matters (the content-driven ribbon height) it is
asserted as a relative ordering, not an exact value.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from raptor.gui.widgets.ribbon import Ribbon, RibbonGroup, RibbonPage

pytestmark = pytest.mark.gui


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _group_with(qapp, n, title="T") -> tuple[RibbonGroup, list]:
    g = RibbonGroup(title)
    acts = [g.add_action(QAction(f"B{i}")) for i in range(n)]
    return g, acts


class TestColumns:
    """Office's anatomy: a large button is a column of its own, small ones
    stack three to a column. `_columns()` is pure, so no screen needed."""

    def test_small_buttons_stack_three_to_a_column(self, qapp):
        g, _acts = _group_with(qapp, 7)
        cols = g._columns()
        assert [len(c) for c in cols] == [3, 3, 1]

    def test_a_large_button_is_a_column_and_ends_the_one_before(self, qapp):
        g = RibbonGroup("g")
        g.add_action(QAction("a"))
        big = g.add_action(QAction("Big"), large=True)
        g.add_action(QAction("b"))
        g.add_action(QAction("c"))
        cols = g._columns()
        assert [len(c) for c in cols] == [1, 1, 2]
        assert cols[1] == [big]

    def test_an_input_control_takes_one_small_slot(self, qapp):
        g = RibbonGroup("g")
        g.add_action(QAction("a"))
        g.add_widget(QPushButton("field"))
        g.add_action(QAction("b"))
        assert [len(c) for c in g._columns()] == [3]


class TestSizeLevels:
    """A group shrinks large -> small -> icon-only before it folds."""

    def _group(self):
        g = RibbonGroup("g")
        first = g.add_action(QAction("First Command"), large=True)
        second = g.add_action(QAction("Second Command"), large=True)
        small = g.add_action(QAction("Small one"))
        field = QPushButton("field")
        g.add_widget(field)
        return g, first, second, small, field

    def test_reduced_keeps_only_the_primary_large(self, qapp):
        from raptor.gui.widgets.ribbon import LARGE, SMALL
        g, *_ = self._group()
        assert g._sizes_at(g.REDUCED) == [LARGE, SMALL, SMALL, SMALL]

    def test_icons_drop_button_labels_but_not_fields(self, qapp):
        from raptor.gui.widgets.ribbon import ICON_ONLY, LARGE
        g, *_ = self._group()
        assert g._sizes_at(g.ICONS) == [LARGE, ICON_ONLY, ICON_ONLY, "small"]

    def test_set_level_changes_the_buttons(self, qapp):
        from PySide6.QtCore import Qt
        g, first, second, small, _field = self._group()
        g.set_level(g.ICONS)
        assert first.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
        assert second.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert small.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        g.set_level(g.FULL)
        assert second.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
        assert small.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon

    def test_width_at_is_measured_without_changing_the_level(self, qapp):
        g, *_ = self._group()
        g.set_level(g.FULL)
        w_full, w_icons = g.width_at(g.FULL), g.width_at(g.ICONS)
        assert w_icons < w_full
        assert g.level() == g.FULL

    def test_a_large_label_wraps_onto_two_lines(self, qapp):
        g, first, *_ = self._group()
        assert first.text() == "First\nCommand"
        g.set_level(g.ICONS)
        g.set_level(g.FULL)
        assert first.text() == "First\nCommand"

    def test_a_renamed_action_keeps_its_wrap(self, qapp):
        """QToolButton copies the action's text back on every change."""
        _g, first, *_ = self._group()
        first.defaultAction().setText("Light Theme")
        assert first.text() == "Light\nTheme"


class TestPlanFit:
    """Which size level each group takes. Pure, so no screen needed."""

    @staticmethod
    def plan(widths, priorities, available, folded=80):
        from raptor.gui.widgets.ribbon import plan_fit
        return plan_fit(widths, priorities, available, folded)

    def test_nothing_shrinks_when_it_fits(self):
        assert self.plan([[300, 200, 100]] * 3, [5, 5, 5], 1000) == [0, 0, 0]

    def test_equal_priorities_shrink_evenly(self):
        # 900 > 650: one step each (600), not one group to icons.
        assert self.plan([[300, 200, 100]] * 3, [5, 5, 5], 650) == [1, 1, 1]

    def test_ties_shrink_the_rightmost_first(self):
        assert self.plan([[300, 200, 100]] * 3, [5, 5, 5], 850) == [0, 0, 1]

    def test_low_priority_runs_ahead(self):
        # Priority 1 runs two levels "ahead": it reaches icons before the
        # others move at all.
        assert self.plan([[300, 200, 100]] * 3, [5, 1, 5], 700) == [0, 2, 0]

    def test_a_wider_level_is_skipped_not_a_dead_end(self):
        """REGRESSION: Hierarchy's reduced level is WIDER (a large button
        turned small puts its label on one line). Stopping there left it at
        full size while Build was squeezed to icons."""
        widths = [[460, 350, 150], [325, 389, 273]]
        levels = self.plan(widths, [5, 2], 760)
        assert levels[1] == 2, levels
        assert levels[0] <= 1, levels

    def test_everything_folds_if_that_is_what_it_takes(self):
        assert self.plan([[300, 200, 100]] * 2, [5, 5], 100, 40) == [3, 3]

    def test_no_width_yet_changes_nothing(self):
        assert self.plan([[300, 200, 100]] * 2, [5, 5], 0) == [0, 0]

    def test_plan_collapse_is_the_one_size_case(self):
        from raptor.gui.widgets.ribbon import plan_collapse
        assert plan_collapse([300, 300, 300], [5, 1, 5], 800, 80) == {1}


class TestRibbonSwitching:
    def test_ribbon_height_is_the_same_on_every_page(self, qapp):
        """Stable chrome: the canvas and both side docks used to jump by
        up to 64 px on every tab switch because the ribbon resized to the
        page. A quiet page and a busy one now get the same height."""
        r = Ribbon()
        quiet, busy = RibbonPage(), RibbonPage()
        qg = RibbonGroup("q")
        qg.add_action(QAction("one"))
        quiet.add_group(qg)
        bg = RibbonGroup("b")
        for i in range(6):
            bg.add_action(QAction(f"B{i}"), large=(i % 2 == 0))
        busy.add_group(bg)
        r.add_page("Quiet", quiet)
        r.add_page("Busy", busy)
        r.resize(1600, 400)
        r.show()
        QApplication.processEvents()

        r.select_page("Quiet")
        QApplication.processEvents()
        h_quiet = r.height()
        r.select_page("Busy")
        QApplication.processEvents()
        h_busy = r.height()
        assert h_quiet >= RIBBON_MIN_TAB_HEIGHT
        assert h_busy == h_quiet, (
            f"the ribbon changed height between pages "
            f"({h_quiet} -> {h_busy})")

    def test_select_page_rejects_unknown_title(self, qapp):
        r = Ribbon()
        p = RibbonPage()
        r.add_page("Only", p)
        with pytest.raises(KeyError):
            r.select_page("Missing")


#: The ribbon must never collapse below its tab strip.
RIBBON_MIN_TAB_HEIGHT = 30


class TestShownWindowHeights:
    """End-to-end: the ribbon a user actually gets.

    The unit tests above build a ribbon by hand, which never goes
    through a real layout pass, so they cannot see a page that is too
    full. This builds the real MainWindow.

    The contract: ONE height on every tab, and every page fits it --
    on a single row of groups -- at each supported width, in both
    themes. A page that does not fit has too much on it: move something
    off it rather than letting the ribbon grow."""

    @pytest.mark.parametrize("width", [1280, 1600, 1920])
    @pytest.mark.parametrize("dark", [False, True])
    def test_one_height_and_every_page_fits(self, qapp, width, dark):
        from raptor.gui.resources.design_tokens import RIBBON
        from raptor.gui.ui.main_window import MainWindow
        from raptor.gui.widgets.ribbon import RibbonButton
        from raptor.gui.resources.design_tokens import ThemeManager
        w = MainWindow()
        try:
            self._check_pages(qapp, w, width, dark, RIBBON, RibbonButton)
        finally:
            # Two pieces of state outlive the window: the ThemeManager
            # singleton, and the persisted appearance/dark_mode setting
            # that every LATER MainWindow reads at construction. Leaving
            # either dark made the theme audit fail, files later.
            from raptor.gui.ui import settings
            settings.set_value("appearance/dark_mode", False)
            ThemeManager.instance().set_dark(False)
            w._dirty = False
            w.close()

    @staticmethod
    def _check_pages(qapp, w, width, dark, RIBBON, RibbonButton):
        if dark:
            w._toggle_theme(True)
        w.resize(width, 800)
        w.show()
        qapp.processEvents()
        r = w._ribbon
        heights, overfull, clipped = {}, [], []
        crowded: list = []
        for i in range(r.tab_bar.count()):
            name = r.tab_bar.tabText(i)
            r.tab_bar.setCurrentIndex(i)
            qapp.processEvents()
            qapp.processEvents()
            page = r._pages[i]
            heights[name] = r.height()
            if page.content_height() > RIBBON["content_height"]:
                overfull.append((name, page.content_height()))
            # At these widths every group is at full size: shrinking is
            # for small screens, not the common case. One exception: the
            # Model tab (Home merged in) is the busiest, and at 1280 px its
            # lowest-priority group, Hierarchy, drops its small buttons'
            # labels (icons and tooltips stay; nothing folds away).
            crowded += [(name, g._label.text(), g.level())
                        for g in page._groups if g.level() != g.FULL
                        and not (width == 1280 and name == "Model"
                                 and g._label.text() == "Hierarchy"
                                 and g.level() in (g.REDUCED, g.ICONS))]
            clipped += [(name, b.text()) for b in
                        page.findChildren(RibbonButton)
                        if b.isVisible()
                        and b.width() < b.sizeHint().width() - 1]
        assert len(set(heights.values())) == 1, \
            f"the ribbon changes height between tabs: {heights}"
        assert heights and next(iter(heights.values())) == \
            RIBBON["tab_height"] + RIBBON["content_height"]
        assert not overfull, f"pages taller than the ribbon: {overfull}"
        assert not crowded, f"groups shrunk at {width} px: {crowded}"
        assert not clipped, f"buttons narrower than their text: {clipped}"


class TestGroupWidthCoversItsColumns:
    """A group's preferred width must cover what its columns need: the
    layout's cached hint went stale once icons and the stylesheet landed,
    and Qt elided labels ("S…d" for Snap to Grid) even at 1920 px."""

    def test_large_button_minimum_width_counts(self, qapp):
        g = RibbonGroup("P")
        btn = g.add_action(QAction("N"), large=True)
        assert g.sizeHint().width() >= btn.minimumWidth()


class TestCompactMode:
    """A narrow window folds lower-priority groups into buttons; nothing
    is lost, and widening puts everything back."""

    @staticmethod
    def _window(qapp, width):
        from raptor.gui.ui.main_window import MainWindow
        w = MainWindow()
        w.resize(width, 800)
        w.show()
        qapp.processEvents()
        return w

    @staticmethod
    def _settle(w, qapp):
        r = w._ribbon
        for i in range(r.tab_bar.count()):
            r.tab_bar.setCurrentIndex(i)
            qapp.processEvents()
            qapp.processEvents()

    @staticmethod
    def _commands(w):
        """Every command name reachable from the ribbon, folded or not."""
        from raptor.gui.widgets.ribbon import RibbonButton
        out = {}
        for i, page in enumerate(w._ribbon._pages):
            title = w._ribbon.tab_bar.tabText(i)
            # The action's text: a button's own label is wrapped or not
            # depending on its size.
            out[title] = sorted(b.defaultAction().text() for b in
                                page.findChildren(RibbonButton)
                                if b.defaultAction().text())
        return out

    @classmethod
    def _squeezed(cls, qapp, px=450):
        """A real window whose ribbon pages get only `px`. The window itself
        cannot be made narrow enough to fold a group -- its minimum (~850 px)
        is absorbed by shrinking alone -- but a page can be."""
        w = cls._window(qapp, 1600)
        cls._squeeze(w, qapp, px)
        return w

    @classmethod
    def _squeeze(cls, w, qapp, px):
        for pg in w._ribbon._pages:
            pg.setMaximumWidth(px or 16777215)
        cls._settle(w, qapp)

    def test_the_table_priorities_reach_the_groups(self, qapp):
        from raptor.gui.command_table import LAYOUT
        w = self._window(qapp, 1600)
        want = {(p.title, g.title): g.priority
                for p in LAYOUT for g in p.groups}
        got = {(w._ribbon.tab_bar.tabText(i), g._label.text()):
               g.collapse_priority
               for i, pg in enumerate(w._ribbon._pages) for g in pg._groups}
        w._dirty = False
        w.close()
        assert got == want

    def test_a_wide_window_folds_nothing(self, qapp):
        w = self._window(qapp, 1600)
        self._settle(w, qapp)
        folded = [(i, [g._label.text() for g in pg.collapsed_groups()])
                  for i, pg in enumerate(w._ribbon._pages)
                  if pg.collapsed_groups()]
        w._dirty = False
        w.close()
        assert not folded, folded

    def test_a_narrow_window_folds_and_keeps_every_command(self, qapp):
        wide = self._window(qapp, 1600)
        self._settle(wide, qapp)
        every = self._commands(wide)
        wide._dirty = False
        wide.close()

        narrow = self._window(qapp, 760)
        self._settle(narrow, qapp)
        shrunk = sum(g.level() != g.FULL or g in pg.collapsed_groups()
                     for pg in narrow._ribbon._pages for g in pg._groups)
        kept = self._commands(narrow)
        narrow._dirty = False
        narrow.close()
        assert shrunk >= 3, "760 px should shrink groups on several pages"
        assert kept == every, "a command went missing when groups shrank"

    @pytest.mark.parametrize("width", [760, 900, 1024])
    def test_one_row_one_height_and_nothing_clipped(self, qapp, width):
        from raptor.gui.resources.design_tokens import RIBBON
        from raptor.gui.widgets.ribbon import RibbonButton
        w = self._window(qapp, width)
        r = w._ribbon
        bad, heights = [], set()
        for i in range(r.tab_bar.count()):
            r.tab_bar.setCurrentIndex(i)
            qapp.processEvents()
            qapp.processEvents()
            page = r._pages[i]
            heights.add(r.height())
            bad += [(r.tab_bar.tabText(i), b.text()) for b in
                    page.findChildren(RibbonButton)
                    if b.isVisible()
                    and b.width() < b.sizeHint().width() - 1]
        w._dirty = False
        w.close()
        assert heights == {RIBBON["tab_height"] + RIBBON["content_height"]}
        assert not bad, bad

    def test_widening_restores_every_group_inline(self, qapp):
        w = self._squeezed(qapp)
        assert any(pg.collapsed_groups() for pg in w._ribbon._pages)
        before = self._commands(w)
        self._squeeze(w, qapp, None)
        after_folded = [pg.collapsed_groups() for pg in w._ribbon._pages]
        after = self._commands(w)
        w._dirty = False
        w.close()
        assert not any(after_folded)
        assert after == before
        assert all(g.level() == g.FULL for pg in w._ribbon._pages
                   for g in pg._groups)

    def test_narrowing_and_widening_repeatedly_does_not_leak(self, qapp):
        w = self._window(qapp, 1600)
        self._settle(w, qapp)
        base = self._commands(w)
        for px in (450, None, 300, None, 700, None):
            self._squeeze(w, qapp, px)
        assert self._commands(w) == base
        w._dirty = False
        w.close()

    def test_a_folded_button_says_what_is_inside(self, qapp):
        w = self._squeezed(qapp)
        btn = next(g._collapsed_button for pg in w._ribbon._pages
                   for g in pg.collapsed_groups())
        title = btn.ribbon_group._label.text()
        shown = btn.text().replace("\n", " ").replace("&&", "&")
        assert shown.startswith(title)
        assert btn.toolTip().startswith(title)
        assert btn.accessibleName() == f"{title} commands"
        assert btn.minimumHeight() >= 32          # the spec's touch target
        w._dirty = False
        w.close()

    def test_the_popup_holds_the_real_group(self, qapp):
        """Not a copy: the same widgets, so state cannot drift."""
        w = self._squeezed(qapp)
        page = next(pg for pg in w._ribbon._pages if pg.collapsed_groups())
        group = page.collapsed_groups()[0]
        btn = group._collapsed_button
        btn.show_popup()
        qapp.processEvents()
        assert btn._popup.isVisible()
        assert group.parent() is btn._popup
        btn.show_popup()                      # a second click closes it
        assert not btn._popup.isVisible()
        w._dirty = False
        w.close()

    def test_a_plain_command_in_the_popup_runs_and_closes_it(self, qapp):
        """Pin a real group: a page with a folded group whose button has a
        default action and no menu."""
        from PySide6.QtGui import QAction
        from raptor.gui.widgets.ribbon import RibbonGroup, RibbonPage, Ribbon
        ribbon = Ribbon()
        page = RibbonPage()
        keep = RibbonGroup("Keep")
        keep.collapse_priority = 9
        keep.add_action(QAction("Stay"))
        wide = QWidget()
        wide.setFixedSize(600, 20)       # makes the page too wide to fit
        keep.add_widget(wide)
        fold = RibbonGroup("Fold")
        fold.collapse_priority = 1
        act = QAction("Do it")
        fired = []
        act.triggered.connect(lambda: fired.append(1))
        fold.add_action(act)
        # Folding must SAVE width to be chosen: a one-button group folded
        # into a button is no narrower.
        wider = QWidget()
        wider.setFixedSize(300, 20)
        fold.add_widget(wider)
        page.add_group(keep)
        page.add_group(fold)
        ribbon.add_page("P", page)
        ribbon.resize(900, 200)
        ribbon.show()
        qapp.processEvents()
        page._relayout()
        assert fold in page.collapsed_groups()
        btn = fold._collapsed_button
        btn.show_popup()
        qapp.processEvents()
        assert btn._popup.isVisible()
        act.trigger()
        qapp.processEvents()
        assert fired == [1]
        assert not btn._popup.isVisible()
        ribbon.close()


class TestFoldedGroupIcons:
    """A folded group is a button in the same family as the large ones, so
    it carries an icon -- taken from its first command that has one."""

    @staticmethod
    def _ink(pm):
        from PySide6.QtGui import QColor
        img = pm.toImage()
        xs, ys = [], []
        for x in range(img.width()):
            for y in range(img.height()):
                if QColor.fromRgba(img.pixel(x, y)).alpha() > 40:
                    xs.append(x)
                    ys.append(y)
        return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1

    def test_the_text_annotation_glyph_fills_its_box(self, qapp):
        """Regression: it was drawn at a fixed 13 pt on the 48 px canvas,
        a 10 x 13 px "T" beside shapes 30-40 px across, and read as an
        empty button. Every annotation tool icon should be comparable."""
        from raptor.gui.resources import icons
        sizes = {}
        for name in ("text_annotation_icon", "rect_annotation_icon",
                     "ellipse_annotation_icon", "arrow_annotation_icon"):
            sizes[name] = self._ink(getattr(icons, name)("#2b6cb0")
                                    .pixmap(48, 48))
        for name, (w, h) in sizes.items():
            assert max(w, h) >= 28, (name, w, h, sizes)

    def test_wide_text_glyphs_still_fit(self, qapp):
        from raptor.gui.resources import icons
        w, h = self._ink(icons.max_icon("#2b6cb0").pixmap(48, 48))
        assert w <= 40 and h <= 40, (w, h)

    def test_every_folded_button_has_an_icon(self, qapp):
        w = TestCompactMode._squeezed(qapp)
        folded = [g for pg in w._ribbon._pages
                  for g in pg.collapsed_groups()]
        assert len(folded) >= 3
        missing = [g._label.text() for g in folded
                   if g._collapsed_button.icon().isNull()]
        w._dirty = False
        w.close()
        assert not missing, missing

    def test_the_icon_comes_from_the_groups_first_command(self, qapp):
        w = TestCompactMode._squeezed(qapp)
        # Batch (Sweep, Compare) folds on a squeezed Simulate tab.
        page = next(pg for pg in w._ribbon._pages
                    if any(g._label.text() == "Batch"
                           for g in pg.collapsed_groups()))
        group = next(g for g in page.collapsed_groups()
                     if g._label.text() == "Batch")
        button = group._collapsed_button
        button.refresh_icon()
        assert button.icon().cacheKey() == \
            w._sweep_action.icon().cacheKey()
        w._dirty = False
        w.close()

    def test_an_ampersand_in_a_title_is_not_a_mnemonic(self, qapp):
        """REGRESSION: "Grid & Focus" showed as "Grid_Focus" when folded."""
        from raptor.gui.widgets.ribbon import CollapsedGroup, RibbonGroup
        button = CollapsedGroup(RibbonGroup("Grid & Focus"))
        assert "&&" in button.text()

    def test_a_group_with_no_icons_gets_the_generic_glyph(self, qapp):
        from PySide6.QtGui import QAction
        from raptor.gui.widgets.ribbon import CollapsedGroup, RibbonGroup
        g = RibbonGroup("Plain")
        g.add_action(QAction("no icon here"))
        button = CollapsedGroup(g)
        assert not button.icon().isNull()

    def test_the_icon_follows_a_theme_change(self, qapp):
        """The icons are repainted wholesale when the theme changes; a copy
        taken at build time would go stale."""
        from raptor.gui.resources.design_tokens import ThemeManager
        from raptor.gui.ui import settings
        w = TestCompactMode._squeezed(qapp)
        try:
            group = next(g for pg in w._ribbon._pages
                         for g in pg.collapsed_groups())
            button = group._collapsed_button
            button.grab()
            before = button.icon().cacheKey()
            w._toggle_theme(True)
            qapp.processEvents()
            button.grab()                 # a paint re-checks the icon
            after = button.icon().cacheKey()
        finally:
            ThemeManager.instance().set_dark(False)
            settings.set_value("appearance/dark_mode", False)
            w._dirty = False
            w.close()
        assert before != after

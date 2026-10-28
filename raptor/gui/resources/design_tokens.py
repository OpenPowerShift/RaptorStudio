"""Design tokens for RAPTOR Studio — geometry, typography, colour, icon sizing.

The simulation-state colours come from Raptor 7's block-status CASE.
ThemeManager provides light/dark switching with live stylesheet updates.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import cast

from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication

SPACING = {
    "2xs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "2xl": 32,
}

RADIUS = {
    "control": 6, "card": 10, "dialog": 12,
}

TYPE = {
    "family": "Inter, Segoe UI, Noto Sans, sans-serif",
    "caption": 11, "body": 13, "label": 12, "title": 18, "metric": 28,
}

ICON = {
    "small": 16, "normal": 24, "large": 32, "ribbon": 36, "stroke": 2,
}

RIBBON = {
    # Office's proportions: a 32 px icon over its label for a large button,
    # a 16 px icon beside it for a small one, three small rows to a column.
    # These are ribbon-specific (not the shared ICON dict). content_height
    # is the ONE height every page gets: 3 + three 24 px rows and their
    # gaps (74) + the group title (16) + 2.
    "tab_height": 30, "content_height": 96, "group_gap": 0,
    "large_action_width": 48, "icon_large": 32, "icon_normal": 16,
    "small_height": 24, "row_gap": 1, "column_gap": 2,
    "group_title_height": 16,
}

DOCK = {
    "min_width": 260, "default_left": 320, "default_right": 360, "default_bottom": 260,
}

#: Simulation-state colours, ported from the block-status CASE in
#: Raptor 7, which names colours directly: LimeGreen for Running
#: (and for Done once `runCompleted`), Red for Repairing, Firebrick for
#: Repair hold, Orange for PM, Gold for PM-hold, Sienna for Idle, Blue
#: for Standby, IndianRed for Done. These are standard colour names, so
#: the RGB values are recovered from the names. The colours are semantic
#: simulation state, so they are the same in light and dark themes. Keys
#: map to block/node states: running/under/over-stressed, cold/warm/hot
#: standby, idle, PM, PM-hold, hold (failed, waiting), repairing, done.
APPENDIX_D_STATE_COLOURS = {
    "running": "#32CD32",          # LimeGreen
    "understressed": "#007000",
    "overstressed": "#70F000",
    #: `NodeStatusType.Degraded` -- Raptor's `Yellow`
    #: (`ChangeNodeState`/`ChangeStatus`/`ChangeDSIStatus`, every
    #: `WHEN Degraded: SetColor(Yellow)` branch). Not a block status:
    #: blocks have no Degraded state, so this key exists for nodes,
    #: hierarchy bodies and their DSI triangles -- including the
    #: after-run average.
    "degraded": "#FFFF00",         # Yellow
    "cold_standby": "#0000F0",     # Blue (cold)
    "warm_standby": "#0000FF",     # Blue (Standby)
    "hot_standby": "#A0E0E0",
    "idle": "#A0522D",             # Sienna
    "maintenance": "#FFA500",      # Orange (PM)
    "pm_hold": "#FFD700",          # Gold (PMhold)
    "hold": "#B22222",             # Firebrick (RepHold)
    "repairing": "#FF0000",        # Red (Repairing)
    "done": "#CD5C5C",             # IndianRed (Done)
}


LIGHT_COLOUR = {
    "nav": "#0B2340",
    "accent": "#1479C9",
    "surface": "#FFFFFF",
    "canvas": "#EDF2F7",
    "text": "#132238",
    "muted": "#5D7080",   # AA (>=4.5) on surface, canvas and group_header
    "border": "#D7E0E8",
    "input_bg": "#FFFFFF",
    "group_header": "#F0F6FC",
    # State colours per Appendix D.
    "operating": APPENDIX_D_STATE_COLOURS["running"],
    "understressed": APPENDIX_D_STATE_COLOURS["understressed"],
    "overstressed": APPENDIX_D_STATE_COLOURS["overstressed"],
    "degraded": APPENDIX_D_STATE_COLOURS["overstressed"],
    "standby": APPENDIX_D_STATE_COLOURS["warm_standby"],
    "maintenance": APPENDIX_D_STATE_COLOURS["maintenance"],
    "idle": APPENDIX_D_STATE_COLOURS["idle"],
    "relationship": "#7454A6",
    "failed": APPENDIX_D_STATE_COLOURS["repairing"],
    # Traffic-light colours for the RESULTS health bar specifically --
    # deliberate exception to the Appendix D rule: Green/Yellow/Red
    # PERCENT is a traffic-light metric and stays red/orange/green,
    # not the block-state colours.
    "result_green": "#218B61",
    "result_yellow": "#D99219",
    "result_red": "#CC4545",
    # Ribbon icon colours for go / stop / warning. Not the Appendix D
    # state colours: LimeGreen is 2:1 on the light ribbon, under the 3:1 an
    # icon needs, so each theme gets its own shade.
    "go": "#1B873F",
    "stop": "#C62828",
    "warn": "#B25E00",
}

DARK_COLOUR = {
    "nav": "#0A1628",
    "accent": "#3399E0",
    "surface": "#1A2332",
    "canvas": "#111927",
    "text": "#E1E8F0",
    "muted": "#8899AA",
    "border": "#2A3A4E",
    "input_bg": "#1E2A3A",
    "group_header": "#1C2A3D",
    # Same Appendix D semantic state colours in both themes.
    "operating": APPENDIX_D_STATE_COLOURS["running"],
    "understressed": APPENDIX_D_STATE_COLOURS["understressed"],
    "overstressed": APPENDIX_D_STATE_COLOURS["overstressed"],
    "degraded": APPENDIX_D_STATE_COLOURS["overstressed"],
    "standby": APPENDIX_D_STATE_COLOURS["warm_standby"],
    "maintenance": APPENDIX_D_STATE_COLOURS["maintenance"],
    "idle": APPENDIX_D_STATE_COLOURS["idle"],
    "relationship": "#7454A6",
    "failed": APPENDIX_D_STATE_COLOURS["repairing"],
    # Same traffic-light exception in dark.
    "result_green": "#34A873",
    "result_yellow": "#E6A91A",
    "result_red": "#E05555",
    "go": "#4CC764",
    "stop": "#F0605D",
    "warn": "#F2A93B",
}


class ThemeManager:
    _instance: ThemeManager | None = None
    #: WeakMethod registry: destroyed widgets' callbacks prune
    #: themselves instead of accumulating across theme toggles. A plain
    #: list, not a WeakSet -- a WeakSet purges WeakMethod entries
    #: immediately because the temporary bound method they wrap dies
    #: right after registration. Registrants must pass a bound method;
    #: lambdas are rejected since only this list would reference them.
    _callbacks: list = []

    def __init__(self):
        self._dark = False

    @classmethod
    def instance(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_dark(self) -> bool:
        return self._dark

    @property
    def colour(self) -> dict:
        return DARK_COLOUR if self._dark else LIGHT_COLOUR

    @property
    def COLOUR(self) -> dict:
        return self.colour

    def toggle(self):
        self._dark = not self._dark
        self._apply()
        self._fire()

    def set_dark(self, dark: bool):
        if self._dark == dark:
            return
        self._dark = dark
        self._apply()
        self._fire()

    def _fire(self):
        dead = []
        for ref in list(self._callbacks):
            method = ref()
            if method is None:
                dead.append(ref)
                continue
            try:
                method(self._dark)
            except RuntimeError:
                dead.append(ref)   # C++ object already deleted
        for ref in dead:
            self._callbacks.remove(ref)

    def on_changed(self, callback):
        """Register a BOUND METHOD (e.g. `self._on_theme_changed`).
        Stored as a WeakMethod so a destroyed widget's entry disappears
        automatically; lambdas/functions are rejected because nothing
        else would keep them alive."""
        import weakref
        if not getattr(callback, "__self__", None):
            raise TypeError(
                "ThemeManager.on_changed needs a bound method (got "
                f"{type(callback).__name__}) -- use "
                "self.<method>, not a lambda")
        self._callbacks.append(weakref.WeakMethod(callback))

    def _apply(self):
        app = cast(QApplication, QApplication.instance())
        if app is None:
            return
        # The stylesheet alone leaves every widget class it doesn't name
        # (QLabel inside a QFormLayout, QSpinBox, QCheckBox, QComboBox
        # popups, ...) on the platform palette, which stays light -- dark
        # surfaces then render near-black text on near-black backgrounds.
        # Setting an application-wide QPalette derived from the same tokens
        # makes every unstyled widget follow the active theme too.
        app.setPalette(_build_palette(self.colour))
        app.setStyleSheet(global_stylesheet())

    @staticmethod
    def _qcolor(hex_str: str) -> QColor:
        return QColor(hex_str)


def _build_palette(c: dict) -> QPalette:
    p = QPalette()
    window = QColor(c["surface"])
    canvas = QColor(c["canvas"])
    text = QColor(c["text"])
    muted = QColor(c["muted"])
    _border = QColor(c["border"])
    input_bg = QColor(c["input_bg"])
    accent = QColor(c["accent"])

    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, input_bg)
    p.setColor(QPalette.ColorRole.AlternateBase, canvas)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, window)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.ToolTipBase, canvas)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor(c["failed"]))
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, window)
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorRole.Link, accent)
    for group in (QPalette.ColorGroup.Disabled,):
        p.setColor(group, QPalette.ColorRole.Text, muted)
        p.setColor(group, QPalette.ColorRole.ButtonText, muted)
        p.setColor(group, QPalette.ColorRole.WindowText, muted)
    return p


def active_colour() -> dict:
    return ThemeManager.instance().colour


class _ColourProxy(Mapping):
    """Dict-like proxy that always reads from active theme. Supports
    `COLOUR["key"]` subscript for backward compat."""
    def __getitem__(self, key):
        return ThemeManager.instance().colour[key]
    def get(self, key, default=None):
        return ThemeManager.instance().colour.get(key, default)
    def __contains__(self, key):
        return key in ThemeManager.instance().colour
    def items(self):
        return ThemeManager.instance().colour.items()
    def keys(self):
        return ThemeManager.instance().colour.keys()
    def values(self):
        return ThemeManager.instance().colour.values()
    def __iter__(self):
        return iter(ThemeManager.instance().colour)
    def __len__(self):
        return len(ThemeManager.instance().colour)


COLOUR = _ColourProxy()


def _rgba(hex_colour: str, alpha: float) -> str:
    """`#RRGGBB` at `alpha`, for a stylesheet."""
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def global_stylesheet() -> str:
    return _build_stylesheet(ThemeManager.instance().colour)


def _build_stylesheet(c: dict) -> str:
    return f"""
    QMainWindow {{
        background-color: {c['canvas']};
    }}
    QMainWindow::separator {{
        width: 5px;
        height: 5px;
        /* Grab affordance: a 5px strip that highlights on hover. An
           invisible or too-thin separator is hard to find and drag. */
        background: {c['canvas']};
    }}
    QMainWindow::separator:hover {{
        background: {c['accent']};
    }}
    QDockWidget {{
        /* No `titlebar-close-icon` override: suppressing the icon would
           remove the close button from a floated dock. */
    }}
    QDockWidget::title {{
        background: {c['surface']};
        border-bottom: 1px solid {c['border']};
        padding: {SPACING['xs']}px {SPACING['sm']}px;
        font-family: {TYPE['family']};
        font-size: {TYPE['label']}px;
        font-weight: 600;
        color: {c['text']};
    }}
    QDockWidget QToolButton {{
        background: transparent;
        border: none;
    }}
    QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: {RADIUS['control']}px;
        padding: {SPACING['xs']}px {SPACING['sm']}px;
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        color: {c['text']};
    }}
    QToolButton:hover {{
        background: {c['canvas']};
        border-color: {c['border']};
    }}
    QToolButton:pressed {{
        background: {c['border']};
    }}
    QToolButton:checked {{
        background: rgba(20, 121, 201, 0.12);
        border-color: {c['accent']};
        color: {c['accent']};
    }}
    QToolButton:disabled {{
        color: {c['muted']};
    }}
    /* Ribbon buttons (not every QToolButton app-wide). Office's look: no
       resting border, a soft fill on hover, and a toggled command shown as
       a FILLED cell (an outlined pill read as a text field). */
    #RibbonButton, #RibbonCollapsedGroup {{
        padding: 1px 5px;
        font-size: {TYPE['label']}px;
        border: 1px solid transparent;
        border-radius: 4px;
    }}
    #RibbonButton[ribbonLarge="true"], #RibbonCollapsedGroup {{
        padding: 3px 4px 2px 4px;
    }}
    #RibbonButton:hover, #RibbonCollapsedGroup:hover {{
        background: {c['canvas']};
        border-color: {c['border']};
    }}
    #RibbonButton:checked {{
        background: {_rgba(c['accent'], 0.16)};
        border-color: {_rgba(c['accent'], 0.45)};
        color: {c['text']};
    }}
    #RibbonButton:checked:hover {{
        background: {_rgba(c['accent'], 0.24)};
    }}
    #RibbonButton[ribbonLarge="true"]::menu-indicator {{
        subcontrol-origin: padding;
        subcontrol-position: bottom right;
    }}
    /* File reads as a tab that opens a page, as in current Office: the
       accent in its text, a fill only under the pointer. A solid accent
       block glared in the dark theme, permanently "lit". */
    #RibbonFileButton {{
        background: transparent;
        color: {c['accent']};
        border: none;
        border-bottom: 2px solid transparent;
        border-radius: 0;
        font-family: {TYPE['family']};
        font-size: {TYPE['body']}px;
        font-weight: 700;
        padding: 4px 14px;
        margin-right: 4px;
    }}
    #RibbonFileButton:hover {{
        background: {c['accent']};
        color: #FFFFFF;
    }}
    QLabel#RibbonFieldLabel {{
        color: {c['muted']};
        font-size: {TYPE['label']}px;
    }}
    QTreeWidget {{
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        color: {c['text']};
        border: none;
        background: {c['surface']};
        alternate-background-color: {c['canvas']};
    }}
    /* The file dialog uses the generic QTreeView/QListView/QTableView
       classes, not the *Widget convenience ones -- without these rules it
       fell back to the platform palette and rendered as a jarring
       black-background/white-foreground mix next to our themed chrome. */
    QTreeView, QListView, QTableView {{
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        color: {c['text']};
        background: {c['input_bg']};
        alternate-background-color: {c['canvas']};
        border: 1px solid {c['border']};
    }}
    QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
        background: {c['accent']};
        color: {c['surface']};
    }}
    QFileDialog {{
        background: {c['surface']};
        color: {c['text']};
    }}
    QTreeWidget::item {{
        padding: 2px 4px;
    }}
    QTreeWidget::item:selected {{
        background: {c['accent']};
        color: {c['surface']};
    }}
    /* No QTreeWidget::branch rules: styling any branch subcontrol makes Qt
       drop the native expand/collapse arrows, leaving no image for those
       states. Unstyled branches draw the platform arrow, which the themed
       app-wide QPalette colours correctly in both themes. */
    QScrollArea {{
        border: none;
        background: {c['surface']};
    }}
    QScrollArea > QWidget > QWidget {{
        background: {c['surface']};
    }}
    QWidget#InspectorContent {{
        background: {c['surface']};
    }}
    QTabWidget::pane {{
        border: 1px solid {c['border']};
        background: {c['surface']};
    }}
    QTabBar::tab {{
        background: {c['canvas']};
        border: 1px solid {c['border']};
        padding: {SPACING['xs']}px {SPACING['sm']}px;
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        color: {c['muted']};
    }}
    /* Hide the Fusion "tear" (rounded corner fans painted around the
       first/last tab when the strip overflows); these read as dark marks
       against the light palette. The scroller rule below handles overflow. */
    QTabBar::tear {{
        image: none;
        border: none;
        background: {c['canvas']};
    }}
    QTabBar::tab:selected {{
        background: {c['surface']};
        color: {c['text']};
        border-bottom: 2px solid {c['accent']};
    }}
    QTableWidget {{
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        gridline-color: {c['border']};
        border: none;
        background: {c['surface']};
    }}
    QTableWidget::item:selected {{
        background: {c['accent']};
        color: {c['surface']};
    }}
    /* The header's own background shows wherever there is no section (past the last column, the empty part of a
       row-number strip, the corner): left unset it fell back to the dark platform palette in the light theme. */
    QHeaderView {{
        background: {c['canvas']};
    }}
    QTableCornerButton::section {{
        background: {c['canvas']};
        border: none;
    }}
    QHeaderView::section {{
        background: {c['canvas']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 2px 6px;
        font-weight: 600;
        color: {c['muted']};
    }}
    QStatusBar {{
        background: {c['nav']};
        /* Fixed bright text: the status bar is dark navy in both themes,
           so the normal `text`/`surface` tokens are too dark to read. */
        color: #EAF2FB;
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        padding: 2px {SPACING['sm']}px;
    }}
    QMenuBar {{
        background: {c['surface']};
        border-bottom: 1px solid {c['border']};
        font-family: {TYPE['family']};
        font-size: {TYPE['body']}px;
        color: {c['text']};
    }}
    QMenu {{
        font-family: {TYPE['family']};
        font-size: {TYPE['body']}px;
        background: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['border']};
    }}
    QMenu::item:selected {{
        background: {c['accent']};
        color: {c['surface']};
    }}
    /* Groups are divided by a hairline, not boxed: a full frame round
       every group made the ribbon read as a form. */
    #RibbonGroup {{
        border: none;
        border-right: 1px solid {c['border']};
        border-radius: 0;
        background: transparent;
    }}
    #RibbonGroupPopup #RibbonGroup {{
        border-right: none;
    }}
    #RibbonGroupTitle {{
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        color: {c['muted']};
    }}
    QGraphicsView {{
        border: none;
        background: {c['canvas']};
    }}
    QLabel#InspectorHeader {{
        background: {c['surface']};
        color: {c['text']};
        font-family: {TYPE['family']};
        font-size: {TYPE['title']}px;
        font-weight: 600;
        padding: {SPACING['sm']}px;
    }}
    QLabel#InspectorEmpty {{
        background: {c['surface']};
        color: {c['muted']};
        font-family: {TYPE['family']};
        font-size: {TYPE['body']}px;
    }}
    /* Inspector banner: read-only, or "results are on screen, your first
       edit will ask about discarding them". */
    QFrame#InspectorLockBanner {{
        background: {c['group_header']};
        border-bottom: 1px solid {c['border']};
    }}
    QLabel#InspectorLockText {{
        background: transparent;
        color: {c['muted']};
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        font-weight: 600;
    }}
    QGroupBox {{
        font-family: {TYPE['family']};
        font-size: {TYPE['label']}px;
        font-weight: 600;
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['card']}px;
        margin-top: {SPACING['md']}px;
        padding-top: {SPACING['md']}px;
        background: {c['surface']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: {SPACING['sm']}px;
        padding: 0 {SPACING['xs']}px;
        color: {c['accent']};
    }}
    QMessageBox {{
        background: {c['surface']};
        color: {c['text']};
    }}
    QMessageBox QLabel {{
        color: {c['text']};
    }}
    QFileDialog {{
        background: {c['surface']};
        color: {c['text']};
    }}
    QDialog {{
        background: {c['surface']};
        color: {c['text']};
    }}
    QComboBox {{
        background: {c['input_bg']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['control']}px;
        padding: 2px 6px;
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
    }}
    /* The drop-down button and its arrow: see `_indicator_rules`. */
    /* The dropdown popup is a separate top-level view that Qt does not
       style from the QComboBox rule above. Styled explicitly so the popup
       matches the active theme instead of the native/OS default. */
    QComboBox QAbstractItemView {{
        background: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['border']};
        selection-background-color: {c['accent']};
        selection-color: {c['surface']};
        outline: none;
    }}
    QLineEdit {{
        background: {c['input_bg']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['control']}px;
        padding: 2px 6px;
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
    }}
    QLineEdit:focus {{
        border-color: {c['accent']};
    }}
    QLabel {{
        background: transparent;
        color: {c['text']};
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
    }}
    /* Draw push buttons from tokens so they match both themes rather than
       the native palette. */
    QPushButton {{
        background: {c['group_header']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['control']}px;
        padding: 4px 12px;
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
    }}
    QPushButton:hover {{
        background: {c['canvas']};
        border-color: {c['accent']};
    }}
    QPushButton:pressed {{
        background: {c['input_bg']};
    }}
    QPushButton:disabled {{
        color: {c['muted']};
        background: {c['canvas']};
    }}
    /* A disabled field must look disabled in both themes: grey text on the canvas colour. Without these the
       stylesheet's own colours override the palette's disabled colours and a locked field looks live. */
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
    QPlainTextEdit:disabled, QTextEdit:disabled {{
        color: {c['muted']};
        background: {c['canvas']};
        border-color: {c['border']};
    }}
    QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled, QGroupBox:disabled {{
        color: {c['muted']};
    }}
    /* Dialog default buttons (Open/Save/OK), marked via
       QPushButton::default, get the accent fill so the primary action
       stands out from Cancel. The native-dialog toolbuttons are themed by
       forcing non-native dialogs; see filedialogs.py. */
    QPushButton:default {{
        background: {c['accent']};
        color: #ffffff;
        border-color: {c['accent']};
        font-weight: 600;
    }}
    QPushButton:default:hover {{
        background: {c['accent']};
        border-color: {c['text']};
    }}

    QAbstractSpinBox {{
        background: {c['input_bg']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['control']}px;
        padding: 2px 6px;
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
        selection-background-color: {c['accent']};
    }}
    /* The spin buttons and their arrows: see `_indicator_rules`. */
    QCheckBox, QRadioButton {{
        color: {c['text']};
        font-family: {TYPE['family']};
        font-size: {TYPE['caption']}px;
    }}
    /* Draw checkbox/radio indicators from tokens: on the light theme's pale
       surface the platform default all but vanishes. Both themes then show
       a clear bordered box with an accent fill when checked. */
    QCheckBox::indicator, QGroupBox::indicator,
    QMenu::indicator:non-exclusive {{
        width: 15px;
        height: 15px;
        border: 1px solid {c['border']};
        border-radius: 3px;
        background: {c['input_bg']};
    }}
    QRadioButton::indicator, QMenu::indicator:exclusive {{
        width: 15px;
        height: 15px;
        border: 1px solid {c['border']};
        border-radius: 8px;
        background: {c['input_bg']};
    }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover,
    QMenu::indicator:hover {{
        border-color: {c['accent']};
    }}
    QCheckBox::indicator:checked,
    QGroupBox::indicator:checked,
    QMenu::indicator:checked,
    QRadioButton::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    QCheckBox::indicator:disabled,
    QRadioButton::indicator:disabled {{
        border-color: {c['border']};
        background: {c['canvas']};
    }}
    /* A checkable ITEM's tick (QTreeWidget/QTableWidget rows -- the Export
       Tables picker) is a different subcontrol from QCheckBox::indicator:
       it is painted by the item-view delegate, so the rules above never
       reached it and it fell back to raw QPalette roles. In the dark theme
       those are dark-on-dark, so ticking a row left no visible mark. */
    QAbstractItemView::indicator,
    QTreeView::indicator,
    QTableView::indicator {{
        width: 15px;
        height: 15px;
        border: 1px solid {c['border']};
        border-radius: 3px;
        background: {c['input_bg']};
    }}
    QAbstractItemView::indicator:hover,
    QTreeView::indicator:hover,
    QTableView::indicator:hover {{
        border-color: {c['accent']};
    }}
    QAbstractItemView::indicator:checked,
    QTreeView::indicator:checked,
    QTableView::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    QAbstractItemView::indicator:disabled,
    QTreeView::indicator:disabled,
    QTableView::indicator:disabled {{
        border-color: {c['border']};
        background: {c['canvas']};
    }}
    QPlainTextEdit, QTextEdit {{
        background: {c['input_bg']};
        color: {c['text']};
        border: 1px solid {c['border']};
        font-family: {TYPE['family']};
    }}
    QToolTip {{
        background: {c['canvas']};
        color: {c['text']};
        border: 1px solid {c['border']};
    }}
    /* Style the tooltip window's own class (QTipLabel) directly. The
       QToolTip rule above does not reliably paint it under every platform
       style, and a widget that carries an unscoped `background` declaration
       can bleed that colour into its tooltip. This gives every tooltip the
       same themed look; status-bar labels therefore keep no unscoped
       background declarations. */
    QTipLabel {{
        background: {c['surface']};
        color: {c['text']};
        border: 1px solid {c['border']};
    }}
    QScrollBar:vertical {{
        background: {c['canvas']};
        width: 13px;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background: {c['muted']};
        border-radius: 5px;
        min-height: 24px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c['accent']};
    }}
    QScrollBar:horizontal {{
        background: {c['canvas']};
        height: 13px;
        border: none;
    }}
    QScrollBar::handle:horizontal {{
        background: {c['muted']};
        border-radius: 5px;
        min-width: 24px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {c['accent']};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0; height: 0; border: none; background: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; border: none;
    }}
    QAbstractScrollArea::corner {{
        background: {c['surface']};
        border: none;
    }}
    /* Tab-strip scroll arrows. The scroller width is the total strip shared
       by both arrows: wide enough to give each a usable hit target, narrow
       enough to leave room for the Inspector's 7-tab strip. */
    QTabBar::scroller {{
        width: 44px;
        background: {c['surface']};
        border-left: 1px solid {c['border']};
    }}
    QTabBar QToolButton {{
        background: {c['group_header']};
        border: 1px solid {c['border']};
        border-radius: {RADIUS['control']}px;
        padding: 0;
        min-width: 18px;
        min-height: 22px;
    }}
    QTabBar QToolButton:hover {{
        background: {c['accent']};
        border-color: {c['accent']};
    }}
    """ + _indicator_rules(c)


def _indicator_rules(c: dict) -> str:
    """The glyphs inside styled sub-controls: spin and combo arrows, the
    tick and the radio dot. Styling a sub-control's box makes Qt stop
    drawing its glyph, so without these the arrows and ticks disappear
    (they did in the dark theme, app-wide). See `indicator_images`."""
    from raptor.gui.resources.indicator_images import indicator_urls
    u = indicator_urls(c)
    if not u:
        return ""
    return f"""
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        subcontrol-origin: border;
        background: {c['group_header']};
        border: 1px solid {c['border']};
        width: 16px;
    }}
    QAbstractSpinBox::up-button {{
        subcontrol-position: top right;
        border-top-right-radius: {RADIUS['control']}px;
    }}
    QAbstractSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: {RADIUS['control']}px;
    }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
        background: {c['canvas']};
    }}
    QAbstractSpinBox::up-arrow {{
        image: url({u['up']});
        width: 9px; height: 9px;
    }}
    QAbstractSpinBox::down-arrow {{
        image: url({u['down']});
        width: 9px; height: 9px;
    }}
    QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{
        image: url({u['up_off']});
    }}
    QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{
        image: url({u['down_off']});
    }}
    /* A read-only spin box (the ribbon's Trials read-out) has no buttons. */
    QAbstractSpinBox[readOnly="true"]::up-button,
    QAbstractSpinBox[readOnly="true"]::down-button {{
        width: 0; border: none;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 18px;
        border: none;
    }}
    QComboBox::down-arrow {{
        image: url({u['down']});
        width: 10px; height: 10px;
    }}
    QComboBox::down-arrow:disabled {{
        image: url({u['down_off']});
    }}
    QToolButton::menu-indicator {{
        image: url({u['down']});
        width: 9px; height: 9px;
    }}
    QToolButton::menu-indicator:disabled {{
        image: url({u['down_off']});
    }}
    QCheckBox::indicator:checked, QGroupBox::indicator:checked,
    QMenu::indicator:non-exclusive:checked,
    QAbstractItemView::indicator:checked {{
        image: url({u['tick']});
    }}
    QCheckBox::indicator:checked:disabled,
    QAbstractItemView::indicator:checked:disabled {{
        image: url({u['tick_off']});
    }}
    QRadioButton::indicator:checked, QMenu::indicator:exclusive:checked {{
        image: url({u['dot']});
    }}
    """

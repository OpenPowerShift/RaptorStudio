"""The ribbon as data: command specs, page layout, and one enablement rule.

`MainWindow._setup_ribbon` used to be ~1,400 lines that built 76 `QAction`s
inline -- text, tooltip, shortcut, icon, handler and enabled state each
written out by hand -- plus ~50 further lines in `_load_into_app` that
enabled them one at a time when a model opened, mirrored by a shorter
disable list in `_close_current_file`. That hand-kept mirror is why 37
actions stayed enabled after File > Close with no model open.

This module replaces both with data and one function of state:

* `CommandSpec` says WHAT a command is (see `command_table.py`);
* `Page` / `Group` / `Btn` / `Custom` say WHERE it goes;
* `AppState` + `PREDICATES` say WHEN it is available;
* `CommandRegistry` builds the actions, keeps the window attribute names
  every other module already uses, and applies enablement from state --
  appending "Unavailable: <reason>" to the tooltip of a disabled command,
  as the design spec requires (`blueprint:125`).

The registry does not import `MainWindow`: handlers are looked up by name
on whatever window it is given, so it is testable with a stub.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from shiboken6 import isValid

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence

# ---------------------------------------------------------------------------
# State and predicates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppState:
    """What command availability depends on.

    Deliberately small. Add a field when a predicate needs it, not before:
    each field is something the window must be able to compute cheaply and
    correctly on demand. Every field is DERIVED from live objects by
    `MainWindow._command_state`, never remembered in a flag -- a flag is
    how a control ends up greyed out forever after a run that ended oddly.
    """
    model_open: bool = False
    #: The open model has a file on disk (Save is meaningful).
    has_file: bool = False
    #: The open model passes validation (False when no model is open).
    valid: bool = False
    #: "idle", "sync" (a batch on the GUI thread) or "background".
    run: str = "idle"
    #: Selected canvas items that can be moved (elements and annotations):
    #: what Align & Distribute acts on.
    selected_movable: int = 0
    #: Selected model elements (blocks, regular nodes, events, hierarchies):
    #: what Mass Edit acts on.
    selected_elements: int = 0
    #: Selected elements Collapse can move into a new hierarchy (blocks,
    #: regular nodes and events). A hierarchy oval is not one of them.
    selected_structural: int = 0
    #: Hierarchy ovals on the level being viewed, and how many are selected.
    hierarchies_here: int = 0
    selected_hierarchies: int = 0
    #: Failure Effects View is open, and the open model has phases.
    fev_active: bool = False
    fev_phased: bool = False
    #: A stepped session is open and a later run is left to jump to.
    step_next_run: bool = False
    #: The background run is on several cores: trials go in batches, so a
    #: single trial cannot be skipped (Jump).
    bg_parallel: bool = False
    #: A stepped session is open (Terminate / Finish / Restart act on it).
    stepping: bool = False
    #: The Results dock currently holds a result set (Clear Results acts
    #: only on those).
    has_results: bool = False
    #: The Simulate ribbon page is the one being shown; its Run panel is
    #: already on screen, so "Run Settings…" has nothing to open.
    run_page: bool = False


def _idle_and_valid(s: AppState) -> bool:
    return s.model_open and s.valid and s.run == "idle"


def _can_simulate(s: AppState) -> bool:
    # A run may start from Failure Effects View; MainWindow leaves FEV
    # before running. Keeping the command enabled means "Run" is never a
    # mystery grey button whose explanation lives on another tab.
    return _idle_and_valid(s)


#: name -> rule. A command's `enabled_when` names one of these.
PREDICATES: dict[str, Callable[[AppState], bool]] = {
    "model_open": lambda s: s.model_open,
    "has_file": lambda s: s.model_open and s.has_file,
    # Start a batch, step, run to a time, fail/repair a block ...: anything
    # that begins or advances a simulation. Needs a valid model and NO run
    # already in progress.
    "can_simulate": _can_simulate,
    # Start a NEW batch while a stepped session is open. Step/Next Time
    # stay available to advance that session; Run/Sweep/Compare would
    # silently discard it.
    "can_start_batch": lambda s: _can_simulate(s) and not s.stepping,
    # Failure Effects View: open it (a valid model, no run), and the commands
    # that only make sense inside it.
    "can_fev": _idle_and_valid,
    "fev_active": lambda s: s.model_open and s.fev_active,
    "fev_phased": lambda s: s.model_open and s.fev_active and s.fev_phased,
    # Stop works on any run (the synchronous batch checks a flag between
    # runs); Pause and Jump exist only for a background run.
    "run_active": lambda s: s.run != "idle",
    # Open / New: replace the model, so not while a run is reading it.
    "not_running": lambda s: s.run == "idle",
    # Close: needs a model, and not while a run is reading it.
    "can_close": lambda s: s.model_open and s.run == "idle",
    "background_run": lambda s: s.run == "background",
    # Terminate / Finish / Restart: they end or restart a stepped session,
    # so they need one (they were enabled with none, and did nothing).
    "stepping": lambda s: s.model_open and s.stepping and s.run == "idle",
    # Jump: the next run of a stepped session, or skip a run of a background batch.
    "jump_available": lambda s: ((s.run == "background" and not s.bg_parallel)
                                 or s.step_next_run),
    # Align needs something to align WITH: two or more movable items.
    "can_align": lambda s: s.model_open and s.selected_movable >= 2,
    "has_selection": lambda s: s.model_open and s.selected_elements >= 1,
    "has_structural_selection": lambda s: (
        s.model_open and s.selected_structural >= 1),
    "has_movable_selection": lambda s: (
        s.model_open and s.selected_movable >= 1),
    "can_mass_edit": lambda s: s.model_open and s.selected_elements >= 2,
    "has_results": lambda s: s.has_results,
    "run_settings_available": lambda s: s.model_open and not s.run_page,
    # Zoom to Hierarchy enters a selected oval.
    "can_zoom_hierarchy": lambda s: (s.model_open
                                     and s.selected_hierarchies >= 1),
    # Extract Hierarchy: one hierarchy oval selected, nothing running.
    "can_extract_hierarchy": lambda s: (s.model_open and s.run == "idle"
                                        and s.selected_hierarchies == 1),
}


def _why_cannot_simulate(s: AppState) -> str:
    if not s.model_open:
        return "open or create a model first"
    if s.run != "idle":
        return "a run is in progress — stop it first"
    return "the model has errors — see the ✗ badge in the status bar"


def _why_cannot_fev(s: AppState) -> str:
    if not s.model_open:
        return "open or create a model first"
    if s.run != "idle":
        return "a run is in progress — stop it first"
    return "the model has errors — see the ✗ badge in the status bar"


#: predicate -> the reason shown after "Unavailable:" in the tooltip of a
#: command whose predicate is false. A callable gets the state, for rules
#: that can fail for more than one reason. A spec's own `unmet` wins.
UNMET: dict[str, str | Callable[[AppState], str]] = {
    "model_open": "open or create a model first",
    "has_file": "this model has not been saved yet — use Save As",
    "can_simulate": _why_cannot_simulate,
    "can_start_batch": lambda s: (
        _why_cannot_simulate(s) if not _can_simulate(s) else
        "a stepped session is open — Finish, Terminate or Restart it first"),
    "can_fev": _why_cannot_fev,
    "fev_active": "open Failure Effects View first",
    "fev_phased": lambda s: ("open Failure Effects View first"
                             if not s.fev_active else "the model has no phases"),
    "run_active": "no run is in progress",
    "not_running": "a run is in progress — stop it first",
    "can_close": lambda s: (
        "open or create a model first" if not s.model_open else
        "a run is in progress — stop it first"),
    "stepping": "nothing is being stepped — press Step (Debug tab) first",
    "background_run": "no run is in progress — press Run (with Animate off "
                      "it runs in the background, where it can be paused)",
    "jump_available": lambda s: (
        "this run is on several cores, which run trials in batches, so one "
        "trial can't be skipped — use Stop, or set Cores to 1"
        if s.run == "background" and s.bg_parallel else
        "Jump goes to the next run of a stepped session (the model has no "
        "later run, or nothing is being stepped) or skips a run of a "
        "background batch"),
    "has_selection": lambda s: (
        "open or create a model first" if not s.model_open else
        "select at least one element first"),
    "has_structural_selection": lambda s: (
        "open or create a model first" if not s.model_open else
        "select at least one block, node or event first"),
    "has_movable_selection": lambda s: (
        "open or create a model first" if not s.model_open else
        "select at least one element first"),
    "can_align": lambda s: (
        "open or create a model first" if not s.model_open else
        "select two or more items first"),
    "can_mass_edit": lambda s: (
        "open or create a model first" if not s.model_open else
        "select two or more elements first"),
    "can_zoom_hierarchy": lambda s: (
        "open or create a model first" if not s.model_open else
        "there are no hierarchies at this level" if not s.hierarchies_here
        else "select a hierarchy oval first"),
    "can_extract_hierarchy": lambda s: (
        "open or create a model first" if not s.model_open else
        "a run is in progress — stop it first" if s.run != "idle" else
        "there are no hierarchies at this level" if not s.hierarchies_here
        else "select one hierarchy oval first"),
    "has_results": "there are no results to clear — run the model first",
    "run_settings_available": (
        "the Run panel is already open on the Simulate tab"),
}


def unmet_reason(predicate: str, state: AppState) -> str:
    reason = UNMET[predicate]
    return reason(state) if callable(reason) else reason


_UNMET_MARK = "\n\nUnavailable: "

def base_tooltip(obj: Any) -> str:
    """The tooltip without the "Unavailable: ..." reason the registry adds
    while a command is disabled. What documentation should print: the
    reason describes the moment, not the command."""
    tip = str(obj.toolTip())
    suffix = obj.property("unmetSuffix") or ""
    return tip[:-len(suffix)] if suffix and tip.endswith(suffix) else tip


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """One command. Everything here is plain data.

    `attr` is the attribute the built `QAction` is stored under on the
    window (`self._open_action`): tests, the theme repaint pass and half
    of `MainWindow` refer to actions by those names, so they are kept.

    `handler` is a dotted path from the window, resolved when the command
    fires (not when it is built), so `"_open_file"` and
    `"_diagram_dock.view.auto_layout"` both work and a handler that is
    replaced later is still the one called. `args` / `kwargs` are passed
    to it; for a `toggled` command the new checked state is appended as
    the last positional argument.
    """
    id: str
    attr: str
    text: str
    tooltip: str = ""
    handler: str = ""
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()
    #: "triggered" (click) or "toggled" (a checkable command).
    on: str = "triggered"
    shortcut: str = ""
    #: Further bindings after `shortcut` (e.g. Redo's Ctrl+Shift+Z).
    more_shortcuts: tuple[str, ...] = ()
    #: A `QKeySequence.StandardKey` name ("Undo", "Redo"), used instead of
    #: or before `shortcut`.
    standard_key: str = ""
    #: "window" (default) or "application" (works whichever widget has
    #: focus; used where the shortcut must not depend on focus).
    context: str = "window"
    checkable: bool = False
    checked: bool = False
    #: "<name in resources.icons>:<colour token>", e.g.
    #: "mouse_arrow_icon:accent", repainted on every theme change. There is
    #: deliberately no way to name a stock QStyle icon: those follow the
    #: desktop's icon theme rather than ours and can vanish in one theme.
    icon: str = ""
    #: Initial enabled state for a command NOT governed by `enabled_when`.
    enabled: bool = True
    #: Alt+<letter> key tip. Fixed per command, so renaming a button can
    #: never reassign a key somebody already learned; "" = no tip. Only a
    #: handful of commands get one -- a letter per button would collide.
    key_tip: str = ""
    #: Name of a predicate in `PREDICATES`; "" = not managed by state.
    enabled_when: str = ""
    #: Overrides the default reason for `enabled_when` in the tooltip.
    unmet: str = ""
    #: Name of a factory in the registry for actions Qt must create
    #: itself (undo / redo come from the undo stack).
    factory: str = ""


@dataclass(frozen=True)
class Btn:
    """A command as a ribbon button."""
    id: str
    large: bool = False


@dataclass(frozen=True)
class Custom:
    """A composite control (menu button, combo, spin box ...) built by a
    named method `builder(group)` on the window, which adds it itself."""
    builder: str


@dataclass(frozen=True)
class Group:
    title: str
    items: tuple[Btn | Custom, ...]
    #: Higher = stays at full size longer when the window is too narrow.
    #: Every group shrinks a step before any shrinks two; within a step
    #: the lowest priority goes first, ties the rightmost (`plan_fit`).
    priority: int = 5


@dataclass(frozen=True)
class Page:
    title: str
    #: Window attribute that receives this page's tab index.
    index_attr: str
    groups: tuple[Group, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _resolve(root: Any, path: str) -> Any:
    obj = root
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


class CommandRegistry:
    """Builds actions from specs and keeps their availability in step
    with `AppState`."""

    def __init__(self, window: Any, specs: Sequence[CommandSpec],
                 factories: dict[str, Callable[[Any, CommandSpec],
                                               QAction]] | None = None
                 ) -> None:
        self.window = window
        self.specs: dict[str, CommandSpec] = {}
        for s in specs:
            if s.id in self.specs:
                raise ValueError(f"duplicate command id {s.id!r}")
            self.specs[s.id] = s
        self.factories = factories or {}
        self.actions: dict[str, QAction] = {}
        #: obj -> (predicate name, unmet text) for everything whose
        #: availability follows state: built actions, and widgets or
        #: composite-control actions registered with `track`.
        self._tracked: list[tuple[Any, str, str]] = []

    # -- building -----------------------------------------------------

    def create_all(self) -> None:
        for spec in self.specs.values():
            action = self._build(spec)
            self.actions[spec.id] = action
            setattr(self.window, spec.attr, action)

    def action(self, command_id: str) -> QAction:
        return self.actions[command_id]

    def _build(self, spec: CommandSpec) -> QAction:
        action = (self.factories[spec.factory](self.window, spec)
                  if spec.factory else QAction(spec.text, self.window))
        icon = self._icon(spec)
        if icon is not None:
            action.setIcon(icon)
        if spec.tooltip:
            action.setToolTip(spec.tooltip)
        self._apply_shortcuts(action, spec)
        if spec.key_tip:
            action.setProperty("keyTip", spec.key_tip)
        if spec.checkable:
            action.setCheckable(True)
            action.setChecked(spec.checked)
        if spec.handler:
            getattr(action, spec.on).connect(self._slot(spec))
        self._apply_initial_state(action, spec)
        return action

    @staticmethod
    def _apply_shortcuts(action: QAction, spec: CommandSpec) -> None:
        if spec.standard_key and not spec.shortcut \
                and not spec.more_shortcuts:
            # A standard key alone binds EVERY platform alternative
            # (Undo is Ctrl+Z, Alt+Backspace and the Undo key), which is
            # what setShortcuts(StandardKey) does and QKeySequence(key)
            # does not.
            action.setShortcuts(getattr(QKeySequence.StandardKey,
                                        spec.standard_key))
        else:
            CommandRegistry._apply_explicit_shortcuts(action, spec)
        if spec.context == "application":
            action.setShortcutContext(
                Qt.ShortcutContext.ApplicationShortcut)

    @staticmethod
    def _apply_explicit_shortcuts(action: QAction,
                                  spec: CommandSpec) -> None:
        keys: list[QKeySequence] = []
        if spec.standard_key:
            keys.append(QKeySequence(
                getattr(QKeySequence.StandardKey, spec.standard_key)))
        if spec.shortcut:
            keys.append(QKeySequence(spec.shortcut))
        keys += [QKeySequence(k) for k in spec.more_shortcuts]
        if len(keys) == 1:
            action.setShortcut(keys[0])
        elif keys:
            action.setShortcuts(keys)

    def _apply_initial_state(self, action: QAction,
                             spec: CommandSpec) -> None:
        if not spec.enabled_when:
            # A factory-made action (undo / redo) follows its own model:
            # the undo stack enables and disables it. Do not override that.
            if not spec.factory:
                action.setEnabled(spec.enabled)
            return
        if spec.enabled_when not in PREDICATES:
            raise ValueError(
                f"{spec.id}: unknown predicate {spec.enabled_when!r}")
        self.track(action, spec.enabled_when, spec.unmet)
        # Until the first refresh nothing is known: start unavailable, as
        # every state-managed command did before.
        action.setEnabled(False)

    def _icon(self, spec: CommandSpec) -> QIcon | None:
        if spec.icon:
            from raptor.gui.resources import icons
            from raptor.gui.resources.design_tokens import active_colour
            name, _, token = spec.icon.partition(":")
            return getattr(icons, name)(active_colour()[token or "accent"])
        return None

    def repaint_icons(self) -> None:
        """Re-draw every table-declared icon in the current theme's colours
        (the drawn icons are cached per colour, so a theme switch needs
        this). Icons the window paints itself afterwards still win."""
        for spec in self.specs.values():
            if spec.icon:
                icon = self._icon(spec)
                if icon is not None:
                    self.actions[spec.id].setIcon(icon)

    def _slot(self, spec: CommandSpec) -> Callable[..., Any]:
        win = self.window
        kwargs = dict(spec.kwargs)

        def call(*signal_args: Any) -> Any:
            fn = _resolve(win, spec.handler)
            if spec.on == "toggled":
                checked = bool(signal_args[0]) if signal_args else False
                return fn(*spec.args, checked, **kwargs)
            return fn(*spec.args, **kwargs)
        return call

    # -- availability -------------------------------------------------

    @staticmethod
    def set_tooltip(obj: Any, base: str) -> None:
        """Change a command's tooltip while keeping the "Unavailable"
        reason it currently carries. Code that rewrites a managed
        command's tooltip (Save's, per open file) must use this rather
        than `setToolTip`, which would silently drop the reason."""
        obj.setToolTip(base + (obj.property("unmetSuffix") or ""))

    def track(self, obj: Any, predicate: str, unmet: str = "") -> None:
        """Make `obj` (an action, or any widget with `setEnabled`) follow
        `predicate`. For composite controls built outside the table."""
        if predicate not in PREDICATES:
            raise ValueError(f"unknown predicate {predicate!r}")
        self._tracked.append((obj, predicate, unmet))
        obj.setEnabled(False)

    def refresh(self, state: AppState,
                only: Iterable[str] | None = None) -> None:
        """Apply `state` to every tracked command, or only to those whose
        predicate is in `only`.

        A full refresh belongs where the whole session changes and nothing
        is running: a model opening or closing (Open / New / Close are
        refused during a run). Anywhere else, name the predicate that
        changed -- e.g. `only={"has_file"}` after Save As -- because a
        full refresh would re-enable commands (Run, Step) that a run in
        progress has legitimately switched off."""
        wanted = None if only is None else set(only)
        for obj, predicate, unmet in self._tracked:
            if wanted is not None and predicate not in wanted:
                continue
            if not isValid(obj):        # its window is closing
                continue
            available = PREDICATES[predicate](state)
            obj.setEnabled(available)
            self._explain(obj, available,
                          unmet or unmet_reason(predicate, state))

    @staticmethod
    def _explain(obj: Any, available: bool, unmet: str) -> None:
        """Say WHY in the tooltip while unavailable; take it back off
        when available. Only the suffix this registry added is touched,
        so a tooltip other code rewrites in between (Save's, per open
        file) is left alone."""
        if not hasattr(obj, "toolTip"):
            return
        tip = obj.toolTip()
        prev = obj.property("unmetSuffix") or ""
        if prev and tip.endswith(prev):
            tip = tip[:-len(prev)]
        suffix = "" if available else f"{_UNMET_MARK}{unmet}"
        obj.setProperty("unmetSuffix", suffix)
        obj.setToolTip(tip + suffix)


# ---------------------------------------------------------------------------
# Page builder
# ---------------------------------------------------------------------------


def build_pages(window: Any, registry: CommandRegistry,
                layout: Iterable[Page]) -> None:
    """Instantiate every page and group in `layout`, place its commands,
    run its custom builders, and add the pages to `window._ribbon`.

    Order in `layout` is tab order.
    """
    from raptor.gui.widgets.ribbon import RibbonGroup, RibbonPage
    for page_spec in layout:
        page = RibbonPage()
        for group_spec in page_spec.groups:
            group = RibbonGroup(group_spec.title)
            group.collapse_priority = group_spec.priority
            page.add_group(group)
            for item in group_spec.items:
                if isinstance(item, Btn):
                    group.add_action(registry.action(item.id),
                                     large=item.large)
                else:
                    getattr(window, item.builder)(group)
        index = window._ribbon.add_page(page_spec.title, page)
        setattr(window, page_spec.index_attr, index)

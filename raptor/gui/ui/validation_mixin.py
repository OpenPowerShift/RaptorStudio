"""Model validation: preflight checks, the issues dialog and the status-bar badge.

Expects on `self`: `_rbd`, `_statusbar`, `_diagram_dock`, `_current_file`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from raptor.gui.ui.modality import exec_dialog


import re
from PySide6.QtCore import (Qt)
from PySide6.QtWidgets import (
    QApplication, QDialog, QPushButton, QLabel, QVBoxLayout,
)

from raptor.core.fileio.rbd import RbdFile


from raptor.gui.resources.design_tokens import (
    active_colour,
)


if TYPE_CHECKING:
    # Mixin methods call Qt on `self`; type-check them as a main window.
    from PySide6.QtWidgets import QMainWindow as _Base
    import PySide6.QtCore
    import PySide6.QtWidgets
    import raptor.gui.commands
    import raptor.gui.ui.diagram
else:
    _Base = object


class ValidationMixin(_Base):
    """Model validation: preflight checks, the issues dialog and the status-bar badge."""

    if TYPE_CHECKING:      # pragma: no cover - typing only
        # What this mixin needs from the host (checked by mypy, so a typo or a removed
        # member is an error here rather than an AttributeError at run time).
        # `x: Any  # really T | None` marks an attribute the host sets to None first and
        # fills in later: declaring it as `T` would be wrong and `T | None` would force
        # a None check at every use, so it stays Any with its real type noted.
        _commands: raptor.gui.commands.CommandRegistry
        _diagram_dock: raptor.gui.ui.diagram.DiagramDock
        _rbd: Any  # really raptor.core.fileio.rbd.RbdFile | None
        _statusbar: PySide6.QtWidgets.QStatusBar
        def _sync_simulate_enabled_state(self) -> Any: ...
        def _update_readiness(self) -> None: ...
        _validation_badge: PySide6.QtWidgets.QPushButton
        _validation_cache: tuple[Any | int, list[str], list[str]]
        _validation_timer: PySide6.QtCore.QTimer
        _validity_cache: tuple[Any | int, bool]
        _validity_dirty_since_check: bool

    def _preflight_issues(self, rbd: RbdFile | None = None) -> list[str]:
        """Model features the engine will reject, found before Run is
        pressed. The checks themselves are a pure function of the model
        and live in `ui/preflight.py` -- this is the window-facing
        wrapper, because the review's "why is Run greyed out?" finding
        could not be fixed while the answer lived inside the widget
        that has the question."""
        from raptor.gui.ui.preflight import preflight_issues
        return preflight_issues(rbd or self._rbd, self._diagram_dock.view,
                                self._preflight_elem_name)

    @staticmethod
    def _preflight_elem_name(kind: str, oid: int) -> str:
        """Best display name for an element id during preflight."""
        from PySide6.QtWidgets import QApplication  # noqa: F401
        return f"{kind} {oid}"

    def _validation_issues(self) -> list[str]:
        """Whole-model validation (not per-level): counting the visible
        level's view dicts would report 'No blocks defined' for a model
        whose blocks all live inside a hierarchy.

        Memoised per model revision, TOGETHER with the warnings: both
        halves used to run the API validator (which rebuilds the engine
        graph) independently, and `_validate_model` ran both, so one
        validation of a 3,500-block model paid for that build four times.
        Every caller of issues and warnings now shares one pass.
        """
        if self._rbd is None:
            return ["No model loaded"]
        rev = getattr(self, "_model_revision", 0)
        cached = getattr(self, "_validation_cache", None)
        if cached is not None and cached[0] == rev:
            return list(cached[1])
        from raptor.api.validate import issues as api_validate
        api_issues = api_validate(self._rbd)
        issues = self._compute_validation_issues(api_issues)
        warnings = self._warnings_from_api(api_issues)
        self._validation_cache = (rev, issues, warnings)
        return list(issues)

    def _compute_validation_issues(self, api_issues) -> list[str]:
        """The GUI's own findings, plus every ERROR from `api_issues`."""
        issues: list[str] = []
        rbd = self._rbd
        if rbd is None:
            return issues
        if "SYSTEM_DATA" not in rbd.sections or \
                not rbd.sections["SYSTEM_DATA"].rows:
            issues.append("Missing SYSTEM_DATA section")
        try:
            block_rows = [r for r in rbd.sections["BLOCK_DATA"].rows
                        if int(float(r.get("ID", "0"))) != 0]
        except (KeyError, ValueError):
            block_rows = []
        # An events-only model (manual: RBDEvents fire once at kickoff,
        # no scheduler process) is fully supported and simulatable with
        # zero blocks -- tests/golden/Events is exactly this shape.
        # Flagging "No blocks defined" for it is a false positive.
        try:
            event_rows = rbd.sections["EVENT_DATA"].rows
        except KeyError:
            event_rows = []
        if not block_rows and not event_rows:
            issues.append("No blocks or events defined (in any "
                          "hierarchy level)")
        # Start->End connectivity is DELEGATED. This used to be a local
        # reachability walk here, wrapped in `except Exception: pass` --
        # which is how a whole capability ended up reachable only through
        # the GUI: an MCP or script user got no Start->End check at all,
        # so a model with broken hierarchy wiring was caught in the window
        # and silently accepted through the API.
        #
        # The local version also called `load_model`, so it was skipped
        # whenever the engine could not build the graph -- exactly the
        # models most likely to have broken wiring. The validator reads the
        # model's own LINK_DATA/NODE_DATA rows instead, so it needs no
        # engine import and cannot raise.
        from raptor.api.validate import _check_start_to_end_reachable
        # The rule is a WARNING: Raptor 7 runs these models, and
        # `preflight_warnings` already lists it. Extending every finding
        # here turned the same warning into a blocking issue and showed
        # it twice (once under problems, once under warnings).
        issues.extend(i.message for i in _check_start_to_end_reachable(
            rbd) if i.severity == "error")
        node_rows = rbd.sections.get("NODE_DATA")
        n_nodes = len(node_rows.rows) if node_rows else 0
        types = {r.get("Type") for r in node_rows.rows} if node_rows \
            else set()
        if n_nodes < 2 or not {"1", "3"} & types:
            issues.append("Model needs Start and End markers")
        # The view-based preflight rules are NOT in the API validator, so
        # they still run; the delegated ones are skipped because the
        # caller already holds their answer (see `preflight_issues`).
        from raptor.gui.ui.preflight import preflight_issues
        issues.extend(preflight_issues(
            rbd, self._diagram_dock.view,
            self._preflight_elem_name, api_issues=api_issues))
        # The API validator owns distribution-parameter and other
        # domain checks the GUI's own input rules do not cover. Without
        # this, "Model OK" could sit over a model `raptor validate`
        # rejects and Run would happily simulate it.
        for issue in api_issues:
            if issue.severity != "error":
                continue
            msg = issue.message.split(" [RAPTOR Studio, ", 1)[0].rstrip()
            text = f"{issue.element}: {msg}"
            if text not in issues:
                issues.append(text)
        return issues

    @staticmethod
    def _warnings_from_api(api_issues) -> list[str]:
        """Non-blocking findings from an already-computed API result."""
        def user_text(msg: str) -> str:
            """Drop the developer source citation from a user-facing warning.

            The API/MCP scrub the citation too, so dialogs and tool replies match;
            the Issues list must not quote source code at a modeller.
            """
            return msg.split(" [RAPTOR Studio, ", 1)[0].rstrip()

        return [f"{i.element}: {user_text(i.message)}" for i in api_issues
                if i.severity == "warning"]

    def _validate_model(self, quiet: bool = False):
        """Compute issues, refresh the badge; clicking the badge (or an
        explicit Validate press when invalid) opens the issues list."""
        if self._rbd is None:
            return
        # This pass supersedes any debounced one: stopping it here also
        # keeps the `processEvents` below from re-entering this method if
        # the timer happened to come due at the same moment.
        timer = getattr(self, "_validation_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        # A full pass over a large model takes seconds, and `quiet` runs
        # happen by themselves after an edit. Say so and show the wait
        # cursor: the window is busy, and an unexplained freeze reads as
        # a hang. (The load path has its own indicator, so skip this
        # there -- it would replace the load dialog's cursor.)
        announce = quiet and not getattr(self, "_loading_model", False)
        if announce:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self._statusbar.showMessage("Checking the model…")
            QApplication.processEvents()
        import time as _time
        started = _time.perf_counter()
        try:
            issues = self._validation_issues()
            # This is the one place the whole model is validated anyway, so
            # hand the answer to `_model_is_valid` rather than making the next
            # `_command_state` repeat the work.
            self._validity_cache = (getattr(self, "_model_revision", 0),
                                    not issues)
            self._validity_dirty_since_check = False
            warnings = self._validation_warnings()
        finally:
            if announce:
                QApplication.restoreOverrideCursor()
        # The pass runs on the GUI thread, so on a large model it is a pause
        # (about 2 s at 5,000 blocks). Wait longer before the next one in
        # proportion: someone making several edits in a row then gets one
        # pause after the last of them, not one between each pair. A small
        # model keeps the 600 ms it always had.
        if timer is not None:
            took = _time.perf_counter() - started
            timer.setInterval(int(min(5000, max(600, 3000 * took))))
        self._set_validation_badge(not issues, issues, warnings)
        # The Run panel's "Not ready — N problem(s)" line is computed
        # from the same issue list, so it is refreshed from here -- the
        # one chokepoint every validation passes through. It used to be
        # wired only to model load and to a Simulate ribbon tab change,
        # so fixing a problem cleared the badge but left the panel still
        # saying "Not ready" until you happened to switch tabs.
        # No-ops when the panel has not been built yet.
        self._update_readiness()
        if not issues and not warnings:
            if announce:
                # Replace the "Checking the model…" message: it has no
                # timeout, so leaving it would keep claiming work that has
                # finished -- e.g. while the user waits on a Weak Link
                # overlay and blames the wrong operation.
                self._statusbar.showMessage("Model checked — no issues", 3000)
            if not quiet:
                self._statusbar.showMessage("No issues: the model is ready to run", 4000)
            return
        if issues:
            self._statusbar.showMessage(
                f"Model has {len(issues)} issue(s) — click Issues for the list")
        elif announce:
            self._statusbar.showMessage(
                f"Model checked — {len(warnings)} warning"
                f"{'s' if len(warnings) != 1 else ''}", 4000)
        if not quiet:
            self._show_issues_dialog(issues, warnings)

    def _locate_issue(self, text: str) -> bool:
        """Select the element an issue is about, on whatever hierarchy
        level it lives. Issues name it as "Block 4 'Pump'" or "node 'split'
        (id=3)"; it is found by id. This used to take the text before the
        first colon as a NAME ("Block 4 'Pump'"), match nothing, and report
        the block as being on another hierarchy level."""
        v = self._diagram_dock.view
        kinds = {"block": "block", "node": "node", "event": "event",
                 "hier": "hier", "hierarchy": "hier"}
        # Links are the one element the diagram draws as a connector, not as
        # an item in the collections `locate_id` searches -- a capacity
        # warning ("Link 29 ...: Nominal flow must be a whole number!")
        # therefore navigated nowhere.
        m = re.search(r"\bLink\s+(\d+)\b", text, re.IGNORECASE)
        if m:
            return self._locate_link(int(m.group(1)))
        m = re.search(r"\b(Block|Node|Event|Hier(?:archy)?)\s+(\d+)\b",
                      text, re.IGNORECASE) or re.search(
            r"\b(block|node|event|hier(?:archy)?)\s+'[^']*'\s*\(id=(\d+)\)",
            text, re.IGNORECASE)
        if m:
            return v.locate_id(kinds[m.group(1).lower()], int(m.group(2)))
        m = re.search(r"\b(block|node)\s+'([^']+)'", text, re.IGNORECASE)
        return m is not None and v.locate_element(m.group(1).lower(),
                                                  m.group(2))

    def _locate_link(self, link_id: int) -> bool:
        """Navigate to a link: its source element, then the connector.

        A link has no row in the block/node/event/hierarchy collections the
        other locators use, so an Issues entry about one ("Nominal flow must
        be a whole number!") could not select anything and the click looked
        broken. Select the source element on its own level, then the
        connector itself, because the flow fields the warning is about are
        edited on the link.
        """
        rbd = self._rbd
        if rbd is None:
            return False
        rows = getattr(rbd.sections.get("LINK_DATA"), "rows", [])
        row = next((r for r in rows if str(r.get("ID")) == str(link_id)), None)
        if row is None:
            return False
        kind = {"Block": "block", "Node": "node", "Event": "event",
                "Hier": "hier", "RBDBlock": "block", "RBDNode": "node",
                "RBDEvent": "event", "RBDHier": "hier"}.get(
                    str(row.get("FromType", "")))
        try:
            from_id = int(float(row.get("FromID", 0)))
        except (TypeError, ValueError):
            from_id = 0
        found = kind is not None and from_id != 0 and \
            self._diagram_dock.view.locate_id(kind, from_id)
        if self._diagram_dock.view.locate_link(link_id):
            return True
        return found

    def _validation_warnings(self) -> list[str]:
        """Non-blocking findings for the Issues list. Header-count drift is
        left out: it is a property of a FILE, re-synced on every save, so
        a model built in the app always has it until its first save, and
        there is nothing the user could do about it.

        Served from the same per-revision pass as the issues: the warning
        side is just the API's `warning` rows, which that pass already
        computed.
        """
        if self._rbd is None:
            return []
        rev = getattr(self, "_model_revision", 0)
        cached = getattr(self, "_validation_cache", None)
        if cached is None or cached[0] != rev:
            self._validation_issues()          # fills the cache
            cached = getattr(self, "_validation_cache", None)
        if cached is None or cached[0] != rev:
            return []
        return [w for w in cached[2] if "WARN: header " not in w]

    def _show_issues_dialog(self, issues: list[str],
                            warnings: list[str] | None = None):
        """Clickable issue list -- problems that stop a run, then warnings
        that do not; picking one locates the element when its name
        appears before the leading colon."""
        warnings = warnings or []
        from PySide6.QtWidgets import (QListWidget)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Issues — {len(issues)} problem(s), "
                           f"{len(warnings)} warning(s)")
        lay = QVBoxLayout(dlg)
        head = QLabel("Click an issue to locate the element "
                      "(when named).")
        head.setObjectName("SectionNote")
        lay.addWidget(head)
        items = QListWidget()
        # Validation messages are often a full sentence with values and a
        # fix. A single elided line hid the actionable half.
        items.setWordWrap(True)
        items.setTextElideMode(Qt.TextElideMode.ElideNone)
        items.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        items.setUniformItemSizes(False)
        items.setSpacing(3)
        for msg in issues:
            items.addItem(msg)
        for msg in warnings:
            items.addItem(f"Warning — {msg}")
        def go(item):
            found = self._locate_issue(item.text())
            if found:
                dlg.accept()
            else:
                self._statusbar.showMessage(
                    "This issue is about the model as a whole, not one "
                    "element", 6000)
        items.itemClicked.connect(go)
        lay.addWidget(items, 1)
        dlg.resize(720, 420)
        exec_dialog(dlg)

    def _schedule_validation(self):
        """Debounced re-validation after edits (model_changed fires per
        keystroke on some editors).

        A load validates once at its end, inside the load indicator, so
        scheduling another pass here would only repeat that work (and can
        fire midway through the rebuild, while the indicator is up).
        """
        if getattr(self, "_loading_model", False):
            return
        self._ensure_validation_timer().start()

    def _ensure_validation_timer(self):
        """Create the debounce timer once, with both slots it must run.

        Both `_validate_model` (refresh the badge and the issue list) and
        `_sync_simulate_enabled_state` (re-enable Run when the fix landed)
        have to fire together; connecting them here keeps the load path and
        the edit path on the same timer instead of each adding a connection.
        """
        if not hasattr(self, "_validation_timer"):
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(600)
            timer.timeout.connect(lambda: self._validate_model(quiet=True))
            timer.timeout.connect(self._sync_simulate_enabled_state)
            self._validation_timer = timer
        return self._validation_timer

    def _set_validation_badge(self, ok: bool, issues: list[str] | None = None,
                              warnings: list[str] | None = None):
        """Persistent clickable right-side badge: green tick or red
        cross with issue count; hover lists the first issues. The ribbon's
        Issues button shows the same state and opens the same list."""
        if not hasattr(self, "_validation_badge"):
            self._validation_badge = QPushButton()
            self._validation_badge.setFlat(True)
            self._validation_badge.setCursor(Qt.CursorShape.PointingHandCursor)
            self._validation_badge.clicked.connect(
                lambda: self._validate_model(quiet=False))
            self._statusbar.addPermanentWidget(self._validation_badge)
        # No model, nothing to vouch for: no "Model OK".
        self._validation_badge.setVisible(self._rbd is not None)
        c = active_colour()
        n_warn = len(warnings or [])
        if ok and n_warn:
            self._validation_badge.setText(
                f"✓ Model OK · {n_warn} warning{'s' if n_warn > 1 else ''}")
            tip = "\n".join((warnings or [])[:6])
        elif ok:
            self._validation_badge.setText("✓ Model OK")
            tip = "Model validates cleanly"
        else:
            n = len(issues or [])
            self._validation_badge.setText(f"✗ Invalid ({n})")
            tip = "\n".join((issues or [])[:6])
            if n > 6:
                tip += f"\n… click for all {n}"
        self._validation_badge.setToolTip(tip)
        colour = c["go"] if ok else c["stop"]
        self._validation_badge.setStyleSheet(
            f"QPushButton {{ color: {colour}; font-weight: 700;"
            f" padding: 0 8px; background: transparent; border: none;"
            f" text-align: right; }}")
        # The ribbon's Issues button carries the same state: a tick when
        # the model can run, a red cross when it cannot.
        act = getattr(self, "_validate_action", None)
        if act is not None:
            from raptor.gui.resources import icons
            act.setIcon(icons.check_badge_icon(c["go"]) if ok
                        else icons.issues_icon(c["stop"]))
            # Through the registry, which keeps any "Unavailable" reason.
            self._commands.set_tooltip(
                act,
                ("No problems stop a run" if ok else
                 f"{len(issues or [])} problem(s) stop a run")
                + (f", {n_warn} warning(s)" if n_warn else "")
                + ". Click for the list (F7)")

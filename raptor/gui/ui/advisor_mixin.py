"""Main-window glue for the Advisor panel: refresh, show, apply a fix.

Expects on `self`: `_rbd`, `_diagram_dock`, `_statusbar`, `_advisor_panel`,
`_advisor_dock`.
"""
from __future__ import annotations

import logging
from typing import Any

from raptor.api.advisor.finding import Finding, Fix, Target

log = logging.getLogger(__name__)

#: A fix's field -> the canvas edit key that changes it (see
#: `raptor.gui.model_edit._ARMS`) and how to turn the fix's value into the
#: key's value given the live object.
_FALSE = ("false", "0", "no", "off")
#: Set once the Advisor has opened itself after a first run (see
#: `AdvisorMixin._introduce_advisor`).
ADVISOR_INTRODUCED = "advisor/introduced"


def _dependency_value(obj: Any, text: str) -> tuple:
    return (0, "None") if text == "None" else (obj.dependency_num, text)


_FIELD_EDITS: dict[tuple[str, str], tuple[str, Any, Any]] = {
    # (kind, field): (edit key, read old from the live object, convert the new text)
    ("block", "dep_type"): ("block dependency",
                            lambda o: (o.dependency_num, o.dep_type), _dependency_value),
    ("block", "uses_pm"): ("block uses pm", lambda o: o.uses_pm,
                           lambda o, t: t.strip().lower() not in _FALSE),
    ("block", "pm_stagger_time"): ("PM stagger", lambda o: o.pm_stagger,
                                   lambda o, t: float(t)),
    ("block", "stock"): ("initial stock", lambda o: o.init_stock, lambda o, t: int(float(t))),
    ("node", "k"): ("node k", lambda o: o.good_paths, lambda o, t: int(float(t))),
}


class AdvisorMixin:
    """Main-window glue for the Advisor panel."""

    _rbd: Any
    _diagram_dock: Any
    _statusbar: Any

    def _build_advisor_dock(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDockWidget
        from raptor.gui.ui.advisor_dock import AdvisorPanel
        self._advisor_panel = AdvisorPanel()
        self._advisor_panel.locate_requested.connect(self._advisor_locate)
        self._advisor_panel.fix_requested.connect(self._advisor_apply_fix)
        self._advisor_panel.ignore_requested.connect(self._advisor_ignore)
        self._advisor_panel.restore_requested.connect(self._advisor_restore)
        self._advisor_dock = QDockWidget("Advisor", self)   # type: ignore[call-overload]
        self._advisor_dock.setWidget(self._advisor_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,  # type: ignore[attr-defined]
                           self._advisor_dock)
        # Tabified beside the Inspector on first show, not here: tabifying
        # a dock that is then hidden leaves QMainWindow an orphan
        # "Inspector | Failure Effects" tab bar that peeks out above the
        # status bar.
        self._advisor_placed = False
        self._advisor_dock.hide()

    def _show_advisor(self) -> None:
        """Show and refresh the Advisor.  The first time, it joins the
        Inspector as a tab (both are about "this element")."""
        dock = getattr(self, "_advisor_dock", None)
        if dock is None:
            return
        if not getattr(self, "_advisor_placed", False):
            self._advisor_placed = True
            inspector = getattr(self, "_inspector_dock", None)
            if inspector is not None and inspector.isVisible():
                self.tabifyDockWidget(inspector, dock)  # type: ignore[attr-defined]
        self._refresh_advisor()
        dock.show()
        dock.raise_()

    # ---- refresh ---------------------------------------------------------
    def _refresh_advisor(self) -> None:
        """Re-run the model rules. Called from the same pass that validates
        the model, so it follows every edit without a second timer."""
        panel = getattr(self, "_advisor_panel", None)
        if panel is None:
            return
        if self._rbd is None:
            panel.set_findings(None)
            self._set_advisor_title(None)
            sync = getattr(self, "_sync_advisor_overlay", None)
            if sync is not None:
                sync()
            return
        from raptor.api.advisor import advise_model, ignore, sort_findings
        try:
            cached = getattr(self, "_api_issues", None)
            validation = (cached[1] if cached is not None
                          and cached[0] == getattr(self, "_model_revision", 0) else None)
            run_findings = self._run_findings()
            findings = sort_findings(advise_model(self._rbd, validation=validation)
                                     + run_findings)
        except Exception:  # noqa: BLE001 - the Advisor must never break an edit
            log.exception("advisor refresh failed")
            findings, run_findings = [], []
        panel.set_findings(findings, [ignore.describe(self._rbd, entry)
                                      for entry in ignore.entries(self._rbd)],
                           note=self._advisor_skipped_note())
        self._set_advisor_title(findings)
        sync = getattr(self, "_sync_advisor_overlay", None)
        if sync is not None:
            sync()
        if run_findings:
            self._introduce_advisor()

    def _advisor_skipped_note(self) -> str:
        """Say so when the downtime checks could not run: they read the
        event log, and without it they were simply missing."""
        ran_without = getattr(getattr(self, "_results", None),
                              "ran_without_event_log", None)
        if ran_without is None or not ran_without():
            return ""
        from raptor.api import run_needs
        return ("What drives the downtime was not checked: that reads the "
                "event log. " + run_needs.gui_hint("attribution"))

    def _introduce_advisor(self) -> None:
        """The first run with something to say opens the Advisor, once per
        install: a user who has never opened View > Advisor would otherwise
        not know the findings exist. Closing it is remembered as usual."""
        from raptor.gui.ui import settings
        if settings.get_bool(ADVISOR_INTRODUCED):
            return
        settings.set_value(ADVISOR_INTRODUCED, True)
        dock = getattr(self, "_advisor_dock", None)
        if dock is not None and not dock.isVisible():
            self._show_advisor()
            self._statusbar.showMessage(
                "The Advisor lists what this run found; "
                "View > Advisor shows or hides it", 8000)

    def _run_findings(self) -> list[Finding]:
        """What the results on screen say: precision, and what drives the
        downtime. A run finding whose fix was applied since those results is
        left out: the results cannot know about the change until the next run."""
        results = getattr(self, "_results", None)
        if results is None or not results.has_results():
            return []
        from raptor.api.advisor import advise_run, ignore
        from raptor.api.run import _rd_to_dict
        report = getattr(results, "_report_data", [])
        per_run = [_rd_to_dict(rd) for rd in report]
        page = getattr(results, "_drivers_page", None)
        attribution = page.attribution() if page is not None else None
        found = advise_run(self._rbd, {}, per_run, attribution=attribution)
        fixed = getattr(self, "_advisor_fixed", None)
        if fixed is None or fixed[0] is not report:
            return found
        return [f for f in found if (f.rule, ignore.target_of(f)) not in fixed[1]]

    def _set_advisor_title(self, findings: list[Finding] | None) -> None:
        dock = getattr(self, "_advisor_dock", None)
        if dock is None:
            return
        n = sum(f.severity in ("error", "warning") for f in findings or [])
        dock.setWindowTitle(f"Advisor ({n})" if n else "Advisor")

    # ---- actions ---------------------------------------------------------
    def _advisor_locate(self, finding: Finding) -> bool:
        view = self._diagram_dock.view
        for target in finding.targets:
            if self._locate_target(view, target):
                return True
        self._statusbar.showMessage(
            "That finding is about a setting, not an element on the diagram", 4000)
        return False

    @staticmethod
    def _locate_target(view: Any, target: Target) -> bool:
        if target.kind in ("block", "node", "event", "hier") and target.id:
            return bool(view.locate_id(target.kind, int(target.id)))
        return False

    def _advisor_apply_fix(self, finding: Finding, fix: Fix) -> bool:
        """Apply a fix as ONE undo step. Returns whether anything changed."""
        if fix.command:
            return self._advisor_run_command(fix.command)
        view = self._diagram_dock.view
        edits = []
        for text in fix.assignments:
            edit = self._edit_for(view, text)
            if edit is None:
                self._statusbar.showMessage(
                    f"'{fix.label}' cannot be applied from here yet: {text}", 6000)
                return False
            edits.append(edit)
        from raptor.gui.ui.diagram_commands import ParamsCmd
        changed = [e for e in edits if e[0] != "experiment" and e[3] != e[4]]
        stack = view._stack
        if changed:
            stack.beginMacro(fix.label)
            try:
                for _kind, key, oid, old, new in changed:
                    stack.push(ParamsCmd(view, key, oid, old, new))
            finally:
                stack.endMacro()
        for kind, key, _oid, _old, new in edits:
            if kind == "experiment":
                self._set_exp_field(key, new)        # type: ignore[attr-defined]
        from raptor.api.advisor import RULES
        info = RULES.get(finding.rule)
        is_run = info is not None and info.phase == "run"
        if is_run:
            self._note_fixed(finding)
        self._statusbar.showMessage(
            f"Applied: {fix.label}" + (" (run again to see its effect)" if is_run else ""),
            5000)
        self._refresh_advisor()
        return bool(changed) or any(e[0] == "experiment" for e in edits)

    def _note_fixed(self, finding: Finding) -> None:
        """Remember a run finding as dealt with until the results change."""
        from raptor.api.advisor import ignore
        report = getattr(getattr(self, "_results", None), "_report_data", None)
        fixed = getattr(self, "_advisor_fixed", None)
        keys = fixed[1] if fixed is not None and fixed[0] is report else set()
        keys.add((finding.rule, ignore.target_of(finding)))
        self._advisor_fixed = (report, keys)

    # ---- dismiss ---------------------------------------------------------
    def _ask_ignore_reason(self, what: str) -> str | None:
        """The reason for dismissing `what`, or None if the user cancelled."""
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "Ignore finding",                            # type: ignore[arg-type]
            f"Why does {what} not apply to this model?\n"
            "The reason is saved with the model, for whoever reviews it.")
        return text.strip() if ok else None

    def _advisor_ignore(self, finding: Finding, whole_rule: bool = False) -> bool:
        """Dismiss `finding` (or every finding of its rule) in this model, as
        one undo step; the reason is kept in the model."""
        from raptor.api.advisor import ignore
        if self._rbd is None:
            return False
        target = "" if whole_rule else ignore.target_of(finding)
        what = (f"every {finding.rule} finding" if whole_rule or not finding.targets
                else f"'{finding.title}' for {finding.targets[0].name}")
        reason = self._ask_ignore_reason(what)
        if reason is None:
            return False
        before = dict(getattr(self._rbd, "advisor", None) or {})
        ignore.ignore(self._rbd, finding.rule, target, reason)
        self._push_advisor_state(before, f"Ignore {finding.rule}")
        self._statusbar.showMessage(
            f"Ignored {what}. 'Show ignored' in the Advisor or Edit > Undo brings it back",
            6000)
        return True

    def _advisor_restore(self, rule: str, target: str) -> bool:
        from raptor.api.advisor import ignore
        if self._rbd is None:
            return False
        before = dict(getattr(self._rbd, "advisor", None) or {})
        if not ignore.restore(self._rbd, rule, target):
            return False
        self._push_advisor_state(before, f"Restore {rule}")
        return True

    def _push_advisor_state(self, before: dict, label: str) -> None:
        """Make the change to `rbd.advisor` (already applied) one undo step."""
        from raptor.gui.ui.diagram_commands import AdvisorStateCmd
        after = dict(getattr(self._rbd, "advisor", None) or {})
        self._rbd.advisor = before
        view = self._diagram_dock.view
        view._stack.push(AdvisorStateCmd(self, before, after, label))

    def _edit_for(self, view: Any, assignment: str) -> tuple | None:
        """`block:2.stock=11` -> (kind, edit key, id, old, new), or None."""
        from raptor.api.paths import PathError, parse_path, split_assignment
        try:
            path, value = split_assignment(assignment)
            parsed = parse_path(path)
        except PathError:
            return None
        if parsed.kind == "system" and parsed.field in ("runs", "num_runs"):
            return ("experiment", "trials", 0, None, int(float(value)))
        spec = _FIELD_EDITS.get((parsed.kind, parsed.field))
        if spec is None or not str(parsed.selector).isdigit():
            return None
        oid = int(parsed.selector)
        obj = (view.blocks if parsed.kind == "block" else view.nodes).get(oid)
        if obj is None:
            return None
        key, read_old, convert = spec
        return (parsed.kind, key, oid, read_old(obj), convert(obj, str(value)))

    def _advisor_run_command(self, command_id: str) -> bool:
        registry = getattr(self, "_commands", None)
        action = registry.action(command_id) if registry is not None and hasattr(
            registry, "action") else None
        if action is None or not action.isEnabled():
            self._statusbar.showMessage("That tool is not available right now", 4000)
            return False
        action.trigger()
        return True

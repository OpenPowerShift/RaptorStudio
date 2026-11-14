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
            return
        from raptor.api.advisor import advise_model, sort_findings
        try:
            cached = getattr(self, "_api_issues", None)
            validation = (cached[1] if cached is not None
                          and cached[0] == getattr(self, "_model_revision", 0) else None)
            findings = sort_findings(advise_model(self._rbd, validation=validation)
                                     + self._run_findings())
        except Exception:  # noqa: BLE001 - the Advisor must never break an edit
            log.exception("advisor refresh failed")
            findings = []
        panel.set_findings(findings)
        self._set_advisor_title(findings)

    def _run_findings(self) -> list[Finding]:
        """What the results on screen say: precision, and what drives the downtime."""
        results = getattr(self, "_results", None)
        if results is None or not results.has_results():
            return []
        from raptor.api.advisor import advise_run
        from raptor.api.run import _rd_to_dict
        per_run = [_rd_to_dict(rd) for rd in getattr(results, "_report_data", [])]
        page = getattr(results, "_drivers_page", None)
        attribution = page.attribution() if page is not None else None
        return advise_run(self._rbd, {}, per_run, attribution=attribution)

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
        self._statusbar.showMessage(f"Applied: {fix.label}", 4000)
        self._refresh_advisor()
        return bool(changed) or any(e[0] == "experiment" for e in edits)

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

"""The Advisor panel: what looks wrong or unwise in the model, with reasons.

It only shows `raptor.api.advisor` findings; the rules live there. Each
finding is a card: a severity glyph (shape as well as colour), the title, the
sentence with the numbers, then its actions -- the suggested fix, *Why?*
(the reasoning and the evidence) and *Show* (select the element).

The first `TOP` findings are shown; the rest sit behind "Show n more", so a
model with forty notes still reads as "these five first".
"""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QToolButton, QVBoxLayout,
    QWidget,
)

from raptor.api.advisor.finding import Finding
from raptor.gui.resources.design_tokens import active_colour

#: How many findings are shown before "Show n more".
TOP = 5
#: The most cards ever built at once (a huge model can have thousands).
MAX_CARDS = 200

GLYPH = {"error": "▲", "warning": "▲", "advice": "●", "info": "○"}
LEVEL_WORD = {"error": "Problem", "warning": "Warning", "advice": "Suggestion", "info": "Note"}
_COLOUR_KEY = {"error": "stop", "warning": "warn", "advice": "accent", "info": "muted"}
BASIS_TAG = {"heuristic": "estimate", "statistical": "statistical", "exact": ""}
BASIS_TIP = {
    "heuristic": "An estimate from the model's data under simplifying assumptions. "
                 "The simulation is the real answer.",
    "statistical": "Drawn from the spread between runs, so it carries a confidence.",
}

EMPTY_TEXT = ("Nothing to report. The Advisor checks the model as you build it: redundancy "
              "that cannot work, placeholder data, spares and crews that cannot meet "
              "demand, maintenance that cannot pay off.")
NO_MODEL_TEXT = "Open or create a model and the Advisor will check it as you build."


def summary_text(findings: list[Finding]) -> str:
    """`2 problems · 3 suggestions · 4 notes` (errors and warnings are problems)."""
    problems = sum(f.severity in ("error", "warning") for f in findings)
    advice = sum(f.severity == "advice" for f in findings)
    notes = sum(f.severity == "info" for f in findings)
    parts = []
    for n, one, many in ((problems, "problem", "problems"),
                         (advice, "suggestion", "suggestions"), (notes, "note", "notes")):
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    return " · ".join(parts) if parts else "No findings"


def evidence_text(finding: Finding) -> str:
    lines = []
    for key, value in finding.evidence.items():
        shown = f"{value:,.4g}" if isinstance(value, float) else str(value)
        lines.append(f"{key.replace('_', ' ')}: {shown}")
    return "\n".join(lines)


class FindingCard(QFrame):
    locate_requested = Signal(object)
    fix_requested = Signal(object, object)
    #: (finding, whole rule?): the user wants it dismissed
    ignore_requested = Signal(object, bool)

    def __init__(self, finding: Finding, parent: QWidget | None = None):
        super().__init__(parent)
        self.finding = finding
        self.setObjectName("AdvisorCard")
        colour = active_colour()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        self.glyph = QLabel(GLYPH[finding.severity])
        self.glyph.setAccessibleName(LEVEL_WORD[finding.severity])
        self.glyph.setToolTip(LEVEL_WORD[finding.severity])
        self.glyph.setStyleSheet(
            f"color: {colour[_COLOUR_KEY[finding.severity]]}; font-size: 14px;")
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.addWidget(self.glyph)

        body = QVBoxLayout()
        body.setSpacing(4)
        outer.addLayout(body, 1)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel(finding.title)
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1)
        tag = BASIS_TAG.get(finding.basis, "")
        self.tag = QLabel(tag)
        self.tag.setVisible(bool(tag))
        self.tag.setToolTip(BASIS_TIP.get(finding.basis, ""))
        self.tag.setStyleSheet(
            f"color: {colour['muted']}; border: 1px solid {colour['border']};"
            " border-radius: 3px; padding: 0 4px; font-size: 11px;")
        head.addWidget(self.tag, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(head)

        self.message = QLabel(finding.message)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.message)

        self.why = QLabel(self._why_text())
        self.why.setWordWrap(True)
        self.why.setStyleSheet(f"color: {colour['muted']};")
        self.why.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.why.hide()
        body.addWidget(self.why)

        body.addLayout(self._build_actions(colour))
        self.setStyleSheet(
            f"#AdvisorCard {{ border-bottom: 1px solid {colour['border']}; }}")

    def _build_actions(self, colour: dict) -> QHBoxLayout:
        finding = self.finding
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.fix_buttons: list[QPushButton] = []
        for fix in finding.fixes:
            button = QPushButton(fix.label)
            button.setToolTip("; ".join(fix.assignments) if fix.assignments
                              else "Opens the tool for this")
            button.clicked.connect(lambda _=False, fx=fix: self.fix_requested.emit(self.finding, fx))
            actions.addWidget(button)
            self.fix_buttons.append(button)
        self.why_button = QToolButton()
        self.why_button.setText("Why?")
        self.why_button.setCheckable(True)
        self.why_button.setAutoRaise(True)
        self.why_button.toggled.connect(self.why.setVisible)
        actions.addWidget(self.why_button)
        self.show_button = QToolButton()
        self.show_button.setText("Show")
        self.show_button.setAutoRaise(True)
        self.show_button.setToolTip("Select what this is about on the diagram")
        self.show_button.setVisible(any(
            t.kind in ("block", "node", "event", "hier") and t.id for t in finding.targets))
        self.show_button.clicked.connect(lambda: self.locate_requested.emit(self.finding))
        actions.addWidget(self.show_button)
        # Dismiss: a menu, not a button, so one click cannot lose a finding.
        # The actions are reachable without exec() (tests trigger them).
        self.ignore_button = QToolButton()
        self.ignore_button.setText("Ignore")
        self.ignore_button.setAutoRaise(True)
        self.ignore_button.setToolTip("Dismiss this finding in this model, with a reason "
                                      "(saved in the model; Edit > Undo or Restore brings it back)")
        self.ignore_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.ignore_button)
        subject = finding.targets[0].name if finding.targets else ""
        self.ignore_one = menu.addAction(
            f"Ignore for {subject}…" if subject else "Ignore this finding…")
        self.ignore_one.triggered.connect(
            lambda: self.ignore_requested.emit(self.finding, False))
        self.ignore_rule = menu.addAction(f"Ignore every {finding.rule} in this model…")
        self.ignore_rule.triggered.connect(
            lambda: self.ignore_requested.emit(self.finding, True))
        self.ignore_rule.setVisible(bool(subject))
        self.ignore_button.setMenu(menu)
        actions.addWidget(self.ignore_button)
        actions.addStretch(1)
        # the rule id is for documentation and scripts: in Why? and the
        # title's tooltip, not in the action row a narrow dock clips
        self.title.setToolTip(f"Rule {finding.rule}")
        return actions

    def _why_text(self) -> str:
        evidence = evidence_text(self.finding)
        return (self.finding.why + (f"\n\n{evidence}" if evidence else "")
                + f"\n\nRule {self.finding.rule}")


class AdvisorPanel(QWidget):
    """The list of findings. `set_findings` replaces what is shown."""

    locate_requested = Signal(object)        # Finding
    fix_requested = Signal(object, object)   # Finding, Fix
    ignore_requested = Signal(object, bool)  # Finding, whole rule
    restore_requested = Signal(str, str)     # rule, target

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._findings: list[Finding] = []
        self._ignored: list[dict] = []
        self._expanded = False
        self._show_ignored = False
        self.cards: list[FindingCard] = []
        self.ignored_rows: list[QWidget] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.summary = QLabel(NO_MODEL_TEXT)
        self.summary.setObjectName("SectionNote")
        self.summary.setWordWrap(True)
        self.summary.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(self.summary)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._cards_layout = QVBoxLayout(self._host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(0)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(self._host)
        lay.addWidget(self._scroll, 1)

        self.more_button = QPushButton()
        self.more_button.setFlat(True)
        self.more_button.clicked.connect(self._toggle_more)
        self.more_button.hide()
        lay.addWidget(self.more_button)

        # what the model's owner dismissed: never silently gone
        self.ignored_button = QPushButton()
        self.ignored_button.setFlat(True)
        self.ignored_button.setToolTip("Findings dismissed in this model, with the reasons")
        self.ignored_button.clicked.connect(self._toggle_ignored)
        self.ignored_button.hide()
        lay.addWidget(self.ignored_button)
        self._ignored_host = QWidget()
        self._ignored_layout = QVBoxLayout(self._ignored_host)
        self._ignored_layout.setContentsMargins(8, 0, 8, 6)
        self._ignored_layout.setSpacing(4)
        self._ignored_host.hide()
        lay.addWidget(self._ignored_host)

    # ---- content ---------------------------------------------------------
    def findings(self) -> list[Finding]:
        return list(self._findings)

    def set_findings(self, findings: list[Finding] | None,
                     ignored: list[dict] | None = None,
                     note: str = "") -> None:
        """Show `findings` (already ordered). `None` means no model is open.
        `ignored` are the model's dismissed entries (rule, target, reason).
        `note` is a line under the summary: what the Advisor could not
        check for these results, and how to let it."""
        self._findings = list(findings or [])
        self._ignored = list(ignored or [])
        if findings is None:
            text = NO_MODEL_TEXT
        elif not findings:
            text = EMPTY_TEXT
        else:
            text = summary_text(self._findings)
        rich = bool(note) and findings is not None
        if rich:
            text = f"{escape(text)}<br><br>{note}"
        self.summary.setTextFormat(Qt.TextFormat.RichText if rich
                                   else Qt.TextFormat.PlainText)
        self.summary.setText(text)
        self._rebuild()

    def refresh_theme(self) -> None:
        """Rebuild the cards: each one takes its colours when it is built."""
        self._rebuild()

    def ignored(self) -> list[dict]:
        return list(self._ignored)

    def _toggle_ignored(self) -> None:
        self._show_ignored = not self._show_ignored
        self._rebuild_ignored()

    def _rebuild_ignored(self) -> None:
        for row in self.ignored_rows:
            self._ignored_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.ignored_rows = []
        n = len(self._ignored)
        self.ignored_button.setVisible(bool(n))
        self.ignored_button.setText(
            f"{'Hide' if self._show_ignored else 'Show'} {n} ignored")
        self._ignored_host.setVisible(bool(n) and self._show_ignored)
        if not (n and self._show_ignored):
            return
        colour = active_colour()
        for entry in self._ignored:
            row = QWidget(self._ignored_host)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            # by name and rule title, not "M-STR-003 · node:26" (U28)
            title = entry.get("title") or entry["rule"]
            subject = entry.get("subject") or entry.get("target") or "every finding"
            reason = entry.get("reason") or "(no reason given)"
            label = QLabel(f"<b>{escape(subject)}</b>: {escape(title)}<br>"
                           f"Ignored because: {escape(reason)}")
            label.setToolTip(entry["rule"])
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {colour['muted']};")
            line.addWidget(label, 1)
            locate = entry.get("locate")
            if locate is not None and locate.kind in ("block", "node", "event", "hier"):
                show = QToolButton(row)
                show.setText("Show")
                show.setAutoRaise(True)
                show.setToolTip(f"Select {subject} on the diagram")
                show.clicked.connect(lambda _=False, e=entry: self.locate_requested.emit(
                    _finding_for(e)))
                line.addWidget(show, 0, Qt.AlignmentFlag.AlignTop)
            restore = QToolButton(row)
            restore.setText("Restore")
            restore.setAutoRaise(True)
            restore.setToolTip("Show this finding again")
            restore.clicked.connect(lambda _=False, e=entry: self.restore_requested.emit(
                e["rule"], e.get("target", "")))
            line.addWidget(restore, 0, Qt.AlignmentFlag.AlignTop)
            self._ignored_layout.addWidget(row)
            self.ignored_rows.append(row)

    def _toggle_more(self) -> None:
        self._expanded = not self._expanded
        self._rebuild()

    def _rebuild(self) -> None:
        for card in self.cards:
            self._cards_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = []
        limit = MAX_CARDS if self._expanded else TOP
        for finding in self._findings[:limit]:
            card = FindingCard(finding, self._host)
            card.locate_requested.connect(self.locate_requested)
            card.fix_requested.connect(self.fix_requested)
            card.ignore_requested.connect(self.ignore_requested)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            card.show()      # a layout shows a new child on the next event-loop turn
            self.cards.append(card)
        hidden = len(self._findings) - TOP
        self.more_button.setVisible(hidden > 0)
        if hidden > 0:
            self.more_button.setText("Show the first five only" if self._expanded
                                     else f"Show {hidden} more")
        self._rebuild_ignored()


def _finding_for(entry: dict) -> Finding:
    """A stand-in finding for an ignored entry, so Show can reuse the
    panel's own locate path."""
    return Finding(rule=entry["rule"], severity="info", category="ignored",
                   title=entry.get("title") or entry["rule"], message="", why="",
                   targets=(entry["locate"],))

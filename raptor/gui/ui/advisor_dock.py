"""The Advisor panel: what looks wrong or unwise in the model, with reasons.

It only shows `raptor.api.advisor` findings; the rules live there. Each
finding is a card: a severity glyph (shape as well as colour), the title, the
sentence with the numbers, then its actions -- the suggested fix, *Why?*
(the reasoning and the evidence) and *Show* (select the element).

The first `TOP` findings are shown; the rest sit behind "Show n more", so a
model with forty notes still reads as "these five first".
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
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
        actions.addStretch(1)
        rule = QLabel(finding.rule)
        rule.setStyleSheet(f"color: {colour['muted']}; font-size: 11px;")
        rule.setToolTip("The rule's id, for the documentation and for scripts")
        actions.addWidget(rule)
        return actions

    def _why_text(self) -> str:
        evidence = evidence_text(self.finding)
        return self.finding.why + (f"\n\n{evidence}" if evidence else "")


class AdvisorPanel(QWidget):
    """The list of findings. `set_findings` replaces what is shown."""

    locate_requested = Signal(object)        # Finding
    fix_requested = Signal(object, object)   # Finding, Fix

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._findings: list[Finding] = []
        self._expanded = False
        self.cards: list[FindingCard] = []

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

    # ---- content ---------------------------------------------------------
    def findings(self) -> list[Finding]:
        return list(self._findings)

    def set_findings(self, findings: list[Finding] | None) -> None:
        """Show `findings` (already ordered). `None` means no model is open."""
        self._findings = list(findings or [])
        if findings is None:
            self.summary.setText(NO_MODEL_TEXT)
        elif not findings:
            self.summary.setText(EMPTY_TEXT)
        else:
            self.summary.setText(summary_text(self._findings))
        self._rebuild()

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
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            card.show()      # a layout shows a new child on the next event-loop turn
            self.cards.append(card)
        hidden = len(self._findings) - TOP
        self.more_button.setVisible(hidden > 0)
        if hidden > 0:
            self.more_button.setText("Show the first five only" if self._expanded
                                     else f"Show {hidden} more")

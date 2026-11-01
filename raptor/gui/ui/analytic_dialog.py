"""Results ▸ Analytical: exact static reliability/availability.

The dialog is a thin front end over `raptor.api.analytical.analyse_rbd`, which
also backs the CLI and the MCP tool. It does not sample and it does not change
the model.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from raptor.api.analytical import analyse_rbd
from raptor.core.analytical.errors import AnalyticalError


def _run_length(rbd: Any) -> float:
    """The model's mission time, or 0 when it is not time-terminated."""
    try:
        row = rbd.sections["SYSTEM_DATA"].rows[0]
        if str(row.get("TerminationType", "TimeSim")) != "TimeSim":
            return 0.0
        value = float(row.get("TermCondition", 0.0))
        return value if value > 0.0 else 1.0
    except Exception:
        return 1.0


def _floats(text: str) -> list[float]:
    if not text.strip():
        return []
    return [float(part) for part in text.split(",") if part.strip()]


class AnalyticDialog(QDialog):
    """Modeless; computes on demand and leaves the model untouched."""

    def __init__(self, win) -> None:
        super().__init__(win)
        self._win = win
        self.setWindowTitle("Analytical Analysis")
        self.setModal(False)
        self.resize(760, 620)
        self.setStyleSheet(
            "QLabel#DialogHeading { font-size: 16px; font-weight: 600; }"
            "QLabel#SectionNote { color: palette(mid); }")
        self._build()

    def _build(self) -> None:  # noqa: PLR0915
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        self._heading = QLabel("Analytical Analysis")
        self._heading.setObjectName("DialogHeading")
        lay.addWidget(self._heading)
        intro = QLabel(
            "Exact static solution for the subset where it exists. No "
            "sampling and no model changes. Reliability mode is "
            "non-repairable; availability mode is steady state with "
            "independent, perfectly repaired components.")
        intro.setWordWrap(True)
        intro.setObjectName("SectionNote")
        lay.addWidget(intro)
        self._status = QLabel(
            "This model is not time-terminated. Enter the mission time you "
            "want to evaluate before computing."
            if _run_length(self._win._rbd) is None else "")
        self._status.setObjectName("SectionNote")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        model_box = QGroupBox("Options")
        form = QFormLayout(model_box)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        lay.addWidget(model_box)
        self._mode = QComboBox()
        self._mode.addItem("Reliability — non-repairable, R(t)", "reliability")
        self._mode.addItem("Availability — steady state", "availability")
        self._mode.setAccessibleName("Analysis mode")
        self._mode.currentIndexChanged.connect(self._mode_changed)
        form.addRow("Mode:", self._mode)

        self._at = QDoubleSpinBox()
        self._at.setRange(0.0, 1e12)
        self._at.setDecimals(6)
        self._at.setValue(_run_length(self._win._rbd))
        self._at.setToolTip(
            "Mission time in the model's native units. Availability mode "
            "ignores it.")
        self._at.setAccessibleName("Mission time")
        form.addRow("Mission time (model units):", self._at)

        self._times = QLineEdit()
        self._times.setPlaceholderText("0, 100, 1000  (optional curve)")
        self._times.setAccessibleName("Curve times")
        form.addRow("Curve at times:", self._times)

        self._bx = QLineEdit("10")
        self._bx.setPlaceholderText("10, 5  (BX life percentages)")
        self._bx.setAccessibleName("BX life percentages")
        form.addRow("BX life (%):", self._bx)

        self._importance = QCheckBox("Exact importance and criticality")
        self._importance.setChecked(True)
        self._importance.setAccessibleName("Exact importance and criticality")
        form.addRow(self._importance)

        self._equation = QCheckBox("Show the path-product equation")
        self._equation.setAccessibleName("Show the path-product equation")
        form.addRow(self._equation)

        self._ignore = QCheckBox(
            "Reliability mode: ignore repairs (first-failure reliability)")
        self._ignore.setAccessibleName("Ignore repairs")
        form.addRow(self._ignore)

        buttons = QHBoxLayout()
        self._compute = QPushButton("Compute")
        self._compute.setDefault(True)
        self._compute.setToolTip("Evaluate the current options (Enter)")
        self._compute.clicked.connect(self.compute)
        copy = QPushButton("Copy")
        copy.setToolTip("Copy the result as plain text")
        copy.setAutoDefault(False)
        copy.clicked.connect(self._copy)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.setDefault(False)
        close.clicked.connect(self.close)
        buttons.addWidget(self._compute)
        buttons.addStretch(1)
        buttons.addWidget(copy)
        buttons.addWidget(close)
        lay.addLayout(buttons)

        result_box = QGroupBox("Result")
        result_layout = QVBoxLayout(result_box)
        result_layout.setContentsMargins(8, 8, 8, 8)
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setAccessibleName("Analytical result")
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._output.setPlaceholderText(
            "Compute to see reliability, MTTF, BX life, importance and "
            "the path-product equation.")
        self._output.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        result_layout.addWidget(self._output)
        lay.addWidget(result_box, 1)
        self.compute()

    def _mode_changed(self) -> None:
        reliability = self._mode.currentData() == "reliability"
        self._at.setEnabled(reliability)
        self._times.setEnabled(reliability)
        self._bx.setEnabled(reliability)
        self._ignore.setEnabled(reliability)
        self._equation.setEnabled(True)
        if self.isVisible():
            self.compute()

    def _values(self) -> dict[str, Any]:
        reliability = self._mode.currentData() == "reliability"
        if reliability:
            try:
                times = _floats(self._times.text())
                bx = _floats(self._bx.text())
            except ValueError:
                raise ValueError("Times and BX values must be comma-separated "
                                 "numbers.") from None
        else:
            times, bx = [], []
        return {
            "mode": self._mode.currentData(),
            "at": float(self._at.value()) if reliability else None,
            "times": times or None,
            "bx": bx or [],
            "importance": self._importance.isChecked(),
            "equation": self._equation.isChecked(),
            "ignore_repairs": self._ignore.isChecked(),
        }

    def compute(self) -> None:
        self._compute.setEnabled(False)
        try:
            result = analyse_rbd(
                self._win._rbd,
                getattr(self._win._rbd, "file_name", "model"),
                **self._values(),
            )
            self._output.setLineWrapMode(
                QPlainTextEdit.LineWrapMode.NoWrap)
            self._output.setPlainText(_summary_text(result))
            name = str(result["model"])
            shown = name if len(name) <= 48 else name[:45] + "…"
            self._heading.setText(
                f"Analytical Analysis — {shown} ({result['mode']})")
            self._heading.setToolTip(name)
            self.setWindowTitle(f"Analytical Analysis — {shown}")
            warning = (result.get("warnings") or [""])[0]
            self._status.setText(
                (f"Exact static result; {result['bdd_nodes']:,} BDD nodes. "
                 "The model was not sampled or changed.")
                + (f"  {warning}" if warning else ""))
            self._status.setToolTip(warning)
        except ValueError as exc:
            self._output.setLineWrapMode(
                QPlainTextEdit.LineWrapMode.WidgetWidth)
            self._output.setPlainText(f"Invalid input:\n\n{exc}")
            self._status.setText("Invalid input: correct the highlighted "
                                 "option and compute again.")
        except AnalyticalError as exc:
            self._output.setLineWrapMode(
                QPlainTextEdit.LineWrapMode.WidgetWidth)
            self._output.setPlainText(f"Not applicable:\n\n{exc}")
            self._status.setText(
                "This model is outside the static solver. Use Simulate for "
                "repairs, PM, spares, resources, dependencies, standby or "
                "phases.")
        except Exception as exc:  # noqa: BLE001 - dialog boundary
            QMessageBox.warning(self, "Analytical Analysis", str(exc))
        finally:
            self._compute.setEnabled(True)

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._output.toPlainText())


def _fmt(value: float | None, digits: int = 9) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}g}"


def _summary_text(result: dict[str, Any]) -> str:  # noqa: C901
    lines = [
        f"{result['model']} — {result['mode']}",
        f"BDD nodes: {result['bdd_nodes']}",
        "",
    ]
    unit = result.get("units") or ""
    suffix = f" {unit}" if unit else ""
    if result["mode"] == "reliability":
        lines += [
            f"time:         {_fmt(result['time'])}{suffix}",
            f"reliability:  {_fmt(result.get('reliability'))}",
            f"pdf:          {_fmt(result.get('pdf'))}",
            f"hazard:       {_fmt(result.get('hazard'))}",
            f"MTTF:         {_fmt(result.get('mttf'))}{suffix}",
        ]
        if result.get("bx"):
            lines.append("BX life:      " + ", ".join(
                f"B{k}={_fmt(v)}{suffix}" for k, v in result["bx"].items()))
    else:
        lines += [
            f"availability: {_fmt(result.get('availability'))}",
            f"unavailability: {_fmt(result.get('unavailability'))}",
        ]
    if result.get("conditional"):
        c = result["conditional"]
        lines.append(
            f"conditional:  R({_fmt(c['age'])} + {_fmt(c['tau'])}) = "
            f"{_fmt(c['reliability'])}")
    if result.get("equation"):
        lines += ["", "Equation:", result["equation"]]
    if result.get("warnings"):
        lines += ["", "Warnings:"]
        lines += [f"  - {w}" for w in result["warnings"]]
    if result.get("assumptions"):
        lines += ["", "Assumptions:"]
        lines += [f"  - {a}" for a in result["assumptions"]]
    if result.get("importance"):
        lines += ["", "Importance:"]
        header = ("name", "birnbaum", "raw", "rrw", "VF", "criticality")
        rows = [[r["name"], _fmt(r.get("birnbaum")), _fmt(r.get("raw")),
                 _fmt(r.get("rrw")), _fmt(r.get("vesely_fussell")),
                 _fmt(r.get("criticality"))] for r in result["importance"]]
        widths = [max(len(header[i]), *(len(row[i]) for row in rows))
                  for i in range(len(header))]
        lines.append("  " + "  ".join(
            header[i].ljust(widths[i]) for i in range(len(header))))
        for row in rows:
            lines.append("  " + "  ".join(
                row[i].ljust(widths[i]) for i in range(len(header))))
    if result.get("times"):
        lines += ["", "Curve:"]
        for point in result["times"]:
            lines.append(f"  t={_fmt(point['time'])}  {_fmt(point['value'])}")
    return "\n".join(lines)


def open_analytic_dialog(win) -> AnalyticDialog:
    """One modeless dialog per window, like the other analysis dialogs."""
    dialog = getattr(win, "_analytic_dialog", None)
    created = dialog is None
    if dialog is None:
        dialog = AnalyticDialog(win)
        win._analytic_dialog = dialog
    dialog._at.setValue(_run_length(win._rbd))
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    if not created:
        dialog.compute()
    return dialog

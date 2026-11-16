"""U17: the run-to-run system state chart can be zoomed and read."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from raptor.api.insight.charts import ChartSpec


def _spec() -> ChartSpec:
    rows = [{"run": 1, "start": 0.0, "end": 100.0, "span": 100.0, "downtime": 0.0, "red": []},
            {"run": 2, "start": 0.0, "end": 100.0, "span": 100.0, "downtime": 10.0,
             "red": [[40.0, 50.0]]}]
    return ChartSpec("ribbon", "Run-to-run system state", "", "time", "runs", series=rows)


def test_the_reading_names_the_outage_and_the_share_of_runs_down():
    from raptor.gui.ui.chart_widget import ribbon_reading
    rows = _spec().series
    text = ribbon_reading(rows, 1, 45.0)
    assert text.startswith("run 2 (90 % up) at 45: down from 40 to 50, for 10")
    assert "1 of 2 runs down at this time" in text
    assert ribbon_reading(rows, 0, 45.0).startswith("run 1 (100 % up) at 45: up")


def test_drag_zooms_and_double_click_shows_the_whole_run():
    from PySide6.QtTest import QTest

    from raptor.gui.ui.chart_widget import ChartWidget
    QApplication.instance() or QApplication([])
    widget = ChartWidget()
    widget.set_spec(_spec())
    widget.resize(700, 300)
    widget.grab()                                  # paint once: lays out the frame
    frame = widget._zoom_frame
    assert frame is not None
    y = int(frame.rect.center().y())
    a, b = int(frame.x(40.0)), int(frame.x(60.0))
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPointF(a, y).toPoint())
    QTest.mouseMove(widget, QPointF(b, y).toPoint())
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPointF(b, y).toPoint())
    assert widget._zoom is not None
    assert widget._zoom[0] == pytest.approx(40.0, abs=1.0)
    assert widget._zoom[1] == pytest.approx(60.0, abs=1.0)
    widget.grab()
    assert widget._zoom_frame.x(40.0) == pytest.approx(widget._zoom_frame.rect.left(), abs=12)
    QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=QPointF(a, y).toPoint())
    assert widget._zoom is None

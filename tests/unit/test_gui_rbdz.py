"""Opening an `.rbdz` puts the saved result set back on the Results dock."""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio import rbdz
from raptor.core.fileio.rbd import load_rbd
from raptor.gui.ui.results import ResultsDock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO, "tests/golden/TwoBlockRedundancy/input.rbd")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_archive_round_trip_restores_the_overview_and_runs(qapp, tmp_path):
    rbd = load_rbd(FIXTURE)
    report_data: list = []
    logs, eor, fin = run_simulation_with_reports(
        rbd, num_runs=2, date="fixed", report_sink=report_data)
    live = ResultsDock()
    live.set_results(logs, eor, fin, report_data=report_data, units="hours",
                     rbd=rbd)

    payload = live.archive_payload()
    assert payload is not None and "summary.json" in payload
    archive = tmp_path / "two.rbdz"
    rbdz.write_archive(archive, "model placeholder", results=payload)

    # The archive's model entry is irrelevant to this test; read the result
    # entries back exactly as the GUI does.
    files, manifest = rbdz.extract_results(archive, tmp_path / "extracted")
    summary = json.loads(files["summary.json"].read_text())
    saved = ResultsDock()
    assert saved.set_archive_results(files, summary, manifest, rbd=rbd)
    assert len(saved._report_data) == 2
    assert saved._cards["Availability"]._value.text() == \
        live._cards["Availability"]._value.text()
    assert len(saved._event_logs) == 2
    assert saved._final_lines

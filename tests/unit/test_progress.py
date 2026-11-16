"""The shared progress and cancel protocol."""
from __future__ import annotations

import threading

import pytest

from raptor.api.progress import Cancelled, Progress, check, report


def test_text_and_fraction():
    p = Progress("attribute", "Working out what caused the downtime", 120, 1000)
    assert p.text() == "Working out what caused the downtime: 120 of 1,000"
    assert p.fraction == pytest.approx(0.12)
    unknown = Progress("setup", "Preparing the model", 0)
    assert unknown.fraction is None and unknown.text() == "Preparing the model"
    assert Progress("variant", "Running variants", 7, 21, "Seals stock = 2").text() \
        == "Running variants: 7 of 21 · Seals stock = 2"
    assert Progress("x", "x", 5, 4).fraction == 1.0


def test_check_and_report():
    check(None)
    event = threading.Event()
    check(event)
    event.set()
    with pytest.raises(Cancelled):
        check(event)
    seen = []
    report(seen.append, "simulate", "Simulating", 1, 2)
    report(None, "simulate", "Simulating", 2, 2)
    assert seen == [Progress("simulate", "Simulating", 1, 2)]

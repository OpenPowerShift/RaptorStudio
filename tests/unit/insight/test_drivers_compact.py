"""`drivers.compact`: a stored attribution cut down for a reader."""
from __future__ import annotations

from raptor.api.insight.drivers import compact


def test_compact_keeps_mean_sem_n_rounds_and_cuts_the_ranked_lists():
    stat = {"mean": 0.30584661544773, "sem": 0.1795613, "min": 0.0, "max": 1.0, "n": 10}
    data = {"elements": [{"dtci": stat}] * 5, "sequences": list(range(5)),
            "capacity": {"elements": [{"share": stat}] * 5, "rule": "proportional"},
            "units": "hours", "total": 1234.56789012}
    out = compact(data, top=2)
    assert out["elements"][0]["dtci"] == {"mean": 0.305847, "sem": 0.179561, "n": 10}
    assert len(out["elements"]) == 2 and out["sequences"] == [0, 1]
    assert len(out["capacity"]["elements"]) == 2 and out["capacity"]["rule"] == "proportional"
    assert out["total"] == 1234.57 and out["units"] == "hours"
    assert data["elements"][0]["dtci"]["min"] == 0.0          # the input is untouched

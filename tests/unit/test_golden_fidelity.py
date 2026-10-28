"""Full-text comparison of generated report output against every golden
fixture that has a real captured End_Of_Run and/or Final_Results report,
not only the subset `test_reports.py` covers.

Each report is compared line-by-line with the shared magnitude-scaled
numeric tolerance (`tests/unit/log_compare.py`). Fixtures whose real
report legitimately differs (recorded in `tests/golden/FIDELITY.toml`) are
`xfail`ed rather than skipped, so the comparison still runs; if such a
fixture starts matching, the test fails on purpose and asks for the
now-stale entry to be deleted.

Golden fixtures live in `tests/golden/` and are resolved through
`tools/golden_fixtures.py`. Usage:
`pytest tests/unit/test_golden_fidelity.py -q`
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import golden_fixtures as gf  # noqa: E402

from raptor.core.engine.runner import run_simulation_with_reports  # noqa: E402
from raptor.core.fileio.rbd import load_rbd  # noqa: E402
from tests.unit.log_compare import (  # noqa: E402
    classify, read_lines, strip_trailing_blank_ish)

GOLDEN = REPO / "tests" / "golden"

with open(GOLDEN / "FIDELITY.toml", "rb") as _f:
    _FIDELITY = tomllib.load(_f).get("report_fidelity", {})


def _mask_header(lines: list[str]) -> list[str]:
    """Blank the report-generation date and the original filename.

    The filename is a capture-session artifact for fixtures resolved from
    `tests/fixtures/`, not a property of the simulation."""
    if not lines:
        return lines
    first = lines[0]
    if first.startswith("Final_Results_Report"):
        prefix = "Final_Results_Report"
    elif first.startswith("End_Of_Run Report"):
        prefix = "End_Of_Run Report"
    else:
        return lines
    lines = list(lines)
    lines[0] = f"{prefix} for STUB_FILE  STUB_DATE"
    return lines


def _diff(mine: list[str], real: list[str]) -> str | None:
    """Return None if mine/real match (modulo fp32-noise); else a one-line
    failure description."""
    mine = _mask_header(strip_trailing_blank_ish(mine))
    real = _mask_header(strip_trailing_blank_ish(real))
    if len(mine) != len(real):
        return f"line count differs: mine={len(mine)} real={len(real)}"
    for i, (m, r) in enumerate(zip(mine, real)):
        if classify(m, r) == "structural":
            return f"structural mismatch at line {i}: mine={m!r} real={r!r}"
    return None


def _fixtures_with_report_axis(axis_pattern: str) -> list[str]:
    out = []
    for name in gf.fixtures_with_legacy():
        if gf.legacy_capture_is_foreign(name):
            # A fixture's whole legacy/ set can belong to a different
            # model, in which case comparing reports tests nothing. See
            # golden_fixtures.MISFILED_LEGACY_CAPTURE.
            continue
        legacy = GOLDEN / name / "legacy"
        if gf.single_match(legacy, axis_pattern) and gf.resolve_rbd_path(name):
            out.append(name)
    return out


_EOR_FIXTURES = _fixtures_with_report_axis("rpt_end_of_run*.txt")
_FR_FIXTURES = _fixtures_with_report_axis("rpt_final_results*.txt")


def _run_and_check(fixture: str, axis: str, real_path: Path, lines) -> None:
    # `required=False`: `single_match` already proved the file is there,
    # and a vanished capture must FAIL this test, not skip it.
    real_lines = read_lines(real_path, required=False)
    failure = _diff(lines, real_lines)
    entry = _FIDELITY.get(fixture, {}).get(axis)

    if failure is None:
        if entry:
            pytest.fail(
                f"{fixture}/{axis} now matches real byte-for-byte, but "
                f"tests/golden/FIDELITY.toml still carries a "
                f"[report_fidelity.{fixture}] {axis!r} entry ({entry!r}). "
                "Delete that entry -- this fixture is fixed."
            )
        return

    if entry:
        pytest.xfail(f"{fixture}/{axis}: {entry}\n(current: {failure})")
    pytest.fail(
        f"{fixture}/{axis}: {failure}\n"
        "No tests/golden/FIDELITY.toml entry covers this -- either this "
        "is a real regression (fix it), or a newly-found gap (add a "
        "[report_fidelity.<fixture>] entry with an evidenced reason)."
    )


@pytest.fixture(scope="module")
def _reports_cache():
    """fixture name -> (eor_lines, fr_lines), computed once per fixture
    (both axes share one simulation run)."""
    cache: dict[str, tuple[list[str], list[str]]] = {}

    def get(name: str) -> tuple[list[str], list[str]]:
        if name not in cache:
            rbd_path = gf.resolve_rbd_path(name)
            rbd_file = load_rbd(str(rbd_path))
            # A capture made with Interim Costs on (an `rpt_interim_cost_*`
            # sibling) runs with a cost sink -- that option changes real's
            # results (same rule as test_event_log_fidelity.py).
            cost_on = bool(list((GOLDEN / name / "legacy").glob("rpt_interim_cost*.txt")))
            _, eor_lines, fr_lines = run_simulation_with_reports(
                rbd_file, date="PROBE", cost_sink=[] if cost_on else None)
            cache[name] = (eor_lines, fr_lines)
        return cache[name]

    return get


@pytest.mark.parametrize("fixture", sorted(_EOR_FIXTURES))
def test_end_of_run_fidelity(fixture, _reports_cache):
    legacy = GOLDEN / fixture / "legacy"
    real_path = gf.single_match(legacy, "rpt_end_of_run*.txt")
    eor_lines, _fr_lines = _reports_cache(fixture)
    _run_and_check(fixture, "eor", real_path, eor_lines)


@pytest.mark.parametrize("fixture", sorted(_FR_FIXTURES))
def test_final_results_fidelity(fixture, _reports_cache):
    legacy = GOLDEN / fixture / "legacy"
    real_path = gf.single_match(legacy, "rpt_final_results*.txt")
    _eor_lines, fr_lines = _reports_cache(fixture)
    _run_and_check(fixture, "fr", real_path, fr_lines)


def test_fidelity_toml_has_no_orphan_entries():
    """Every [report_fidelity.<fixture>] entry names a fixture this
    sweep actually covers -- catches a typo'd fixture name (silently
    inert: any FIDELITY.toml entry for a name not in _EOR_FIXTURES/
    _FR_FIXTURES would never be looked up by _run_and_check)."""
    known = set(_EOR_FIXTURES) | set(_FR_FIXTURES)
    orphans = set(_FIDELITY) - known
    assert not orphans, (
        f"FIDELITY.toml has [report_fidelity.*] entries for fixtures this "
        f"sweep doesn't cover (typo, or the fixture's real capture moved/"
        f"disappeared): {sorted(orphans)}"
    )

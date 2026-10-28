"""Tests for the shared sweep harness (`sweeps/_sweep_base.py`).

These are the guard on the harness itself. The bug that made this
module necessary was a *silent* one: four sweeps recorded results in
four different shapes and the wrapper only read a stdout line, so a
mismatch between the two never failed a build.

The load-bearing cases below are the ones the old conventions got wrong:

* A `"FAIL"` **string** is not a failure you can tally. It is truthy,
  so `not r[1]` said "not a failure" and a real failure reported as
  zero. `Recorder.record` now refuses it outright.
* `ok=False` **is** counted as a failure, by the only code path that
  counts failures.
* XFAIL (`ok=None`) is neither a pass nor a failure -- otherwise the
  chapter sweep's documented engine gaps would either inflate the pass
  count or fail the build.
* A check that records its own failure and returns normally keeps that
  failure, instead of having it overwritten by `record(tid, True)`.

`_sweep_base` imports neither PySide6 nor raptor at module scope, so
these run without a QApplication and without patching any Qt class.
"""
from __future__ import annotations

import io
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "sweeps"))

import _sweep_base as sb  # noqa: E402

#: The exact line `tests/integration/test_ui_sweeps.py` reads. Duplicated
#: here on purpose: if the harness and the wrapper ever disagree about
#: the format, this test fails even if nobody runs a real sweep.
TOTAL_RE = re.compile(r"TOTAL:\s*(\d+) passed,\s*(\d+) failed")


def _report(rec: sb.Recorder) -> tuple[int, str]:
    buf = io.StringIO()
    code = rec.report(stream=buf)
    return code, buf.getvalue()


# ------------------------------------------------------------ the schema


def test_recorder_refuses_a_status_string() -> None:
    """The old `(tid, "PASS"|"FAIL", detail)` convention must not be
    recordable: a non-empty string is truthy, so a `not r[1]` failure
    count read `"FAIL"` as a pass."""
    rec = sb.Recorder()
    for bad in ("FAIL", "PASS", "fail", "XFAIL", "True", 0, 1, []):
        with pytest.raises(TypeError):
            rec.record("some.check", bad)
    assert rec.results == [], "a rejected result must not be recorded"


def test_recorder_refuses_a_status_string_where_it_would_be_tallied() -> None:
    """The TypeError is not decorative: had it been accepted, the old
    footer (`fails = [r for r in RESULTS if not r[1]]`) would have
    reported zero failures for it."""
    rec = sb.Recorder()
    with pytest.raises(TypeError):
        rec.record("some.check", "FAIL")
    # Nothing that a truthiness test could mis-read got in: the old
    # footer filtered failures with `not r[1]`.
    assert [r for r in rec.results if not r.ok] == []


def test_ok_false_is_counted_as_a_failure() -> None:
    rec = sb.Recorder()
    rec.record("a", True)
    rec.record("b", False, "boom")
    code, out = _report(rec)
    assert [r.tid for r in rec.failures] == ["b"]
    assert [r.tid for r in rec.passes] == ["a"]
    assert code == 1
    m = TOTAL_RE.search(out)
    assert m is not None, out
    assert (int(m.group(1)), int(m.group(2))) == (1, 1)
    assert "FAIL b" in out and "boom" in out


def test_xfail_is_neither_a_pass_nor_a_failure() -> None:
    rec = sb.Recorder()
    rec.record("gap", sb.XFAIL, "engine gap: cold-standby propagation")
    rec.record("ok", True)
    assert rec.xfails == [rec.results[0]]
    assert rec.passes == [rec.results[1]]
    assert rec.failures == []
    code, out = _report(rec)
    assert code == 0, "an XFAIL must not fail the sweep"
    m = TOTAL_RE.search(out)
    assert (int(m.group(1)), int(m.group(2))) == (1, 0)
    assert "XFAIL" in out and "engine gap" in out


def test_skip_is_not_a_result() -> None:
    """SKIP means nothing was checked. It must not be reachable as a
    pass or a failure, or a sweep could pass by skipping its work."""
    rec = sb.Recorder()
    rec.skip("starter.file", "exercise starter: PHASES empty by design")
    assert rec.results == []
    assert rec.passes == rec.failures == rec.xfails == []
    code, out = _report(rec)
    assert code == 0
    assert tuple(int(g) for g in TOTAL_RE.search(out).groups()) == (0, 0)
    assert "SKIP starter.file" in out


def test_run_check_keeps_a_self_recorded_failure() -> None:
    """The latent bug: a check that records its own failure and returns
    normally used to have that failure overwritten by the trailing
    `record(tid, True)` -- sweeps 1/4/5 had no guard at all."""
    rec = sb.Recorder()

    def soft_check():
        rec.record("soft", False, "expected some rows hidden, got none")

    rec.run_check("soft", soft_check)
    assert len(rec.results) == 1
    assert rec.results[0].ok is False
    assert rec.failures and not rec.passes


def test_run_check_records_a_pass_when_the_check_is_silent() -> None:
    rec = sb.Recorder()
    rec.run_check("quiet", lambda: None)
    assert [(r.tid, r.ok) for r in rec.results] == [("quiet", True)]


def test_run_check_records_an_exception_as_a_failure() -> None:
    rec = sb.Recorder()

    def boom():
        raise AssertionError("kaboom")

    rec.run_check("boom", boom)
    assert rec.results[0].ok is False
    assert "kaboom" in rec.results[0].detail
    assert rec.report(stream=io.StringIO()) == 1


def test_run_check_does_not_double_record() -> None:
    rec = sb.Recorder()

    def self_passes():
        rec.record("twice", True)

    rec.run_check("twice", self_passes)
    assert len(rec.results) == 1


# ------------------------------------------------------------ the report


def test_report_prints_exactly_one_total_line() -> None:
    """The wrapper asserts one TOTAL line. Two of them would mean two
    tally paths, which is the whole class of bug being closed."""
    rec = sb.Recorder()
    rec.record("a", True)
    rec.record("b", False)
    rec.record("c", sb.XFAIL)
    rec.skip("d")
    _, out = _report(rec)
    assert len(TOTAL_RE.findall(out)) == 1, out


def test_report_exit_code_tracks_failures_only() -> None:
    assert _report(sb.Recorder())[0] == 0, "empty sweep is not a failure"
    rec = sb.Recorder()
    rec.record("a", True)
    rec.skip("b")
    rec.record("c", sb.XFAIL)
    assert _report(rec)[0] == 0
    rec.record("d", False)
    assert _report(rec)[0] == 1


def test_report_captures_passed_modal_boxes() -> None:
    """An unexpected `critical` box is a real finding; the sweeps have
    always printed them, so the shared footer must too."""
    rec = sb.Recorder()
    rec.record("a", True)
    boxes = [("critical", "Simulation Failed")]
    buf = io.StringIO()
    rec.report(msgboxes=boxes, stream=buf)
    out = buf.getvalue()
    assert "Modal message boxes captured during run:" in out
    assert "QMessageBox.critical: Simulation Failed" in out


# ------------------------------------------------------------- stub config


def test_capture_information_flag_controls_what_is_captured() -> None:
    """ui_sweep6 classifies a chapter from the captured text, so an
    informational box leaking into that list would change a verdict."""
    src = pathlib.Path(sb.__file__).read_text(encoding="utf-8")
    assert "capture_information: bool = True" in src
    # and the sweep that needs the narrower list asks for it
    sweep6 = pathlib.Path(sb.__file__).with_name("ui_sweep6.py")
    assert "capture_information=False" in sweep6.read_text(encoding="utf-8")


def test_preflight_run_anyway_is_opt_in() -> None:
    """With `exec` stubbed, a real `clickedButton()` is always None, so
    the default is 'preflight issues cancel the run' -- what sweeps 1-5
    have always exercised. Only the chapter sweep wants Run Anyway."""
    src = pathlib.Path(sb.__file__).read_text(encoding="utf-8")
    assert "preflight_run_anyway: bool = False" in src
    sweep6 = pathlib.Path(sb.__file__).with_name("ui_sweep6.py")
    assert "preflight_run_anyway=True" in sweep6.read_text(encoding="utf-8")


def test_module_does_not_import_qt_at_module_scope() -> None:
    """`_sweep_base` must be importable without a QApplication: this
    test module (and any future non-GUI consumer) would otherwise have
    to stand one up, and `install_modal_stubs` would patch the Qt
    classes for the whole pytest session."""
    src = pathlib.Path(sb.__file__).read_text(encoding="utf-8")
    offenders = [
        ln for ln in src.splitlines()
        if ln.startswith(("import ", "from "))
        and ("PySide6" in ln or ln.startswith(("import raptor", "from raptor")))
    ]
    assert offenders == [], (
        f"{offenders} -- these must stay inside the functions that need them")
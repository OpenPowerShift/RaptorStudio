"""Shared harness for the UI smoke sweeps in this directory.

Before this module the six `ui_sweepN.py` scripts each carried their own
copy of the same three things:

1. ~20 lines of modal-dialog neutralisation (`QDialog.exec`,
   `QFileDialog.exec`, `QMessageBox.question`, `dialogs.question_box`,
   plus a message-box capture list).
2. A `run_check(tid, fn)` helper.
3. A hand-rolled footer that tallies the results and prints `TOTAL:`.

And they did NOT agree on (3)'s input shape. Four different `RESULTS`
schemas sat behind the single stdout regex in
`tests/integration/test_ui_sweeps.py`:

* `ui_sweep1/2/3`: `(tid, "PASS"|"FAIL", detail)` -- a **string**;
* `ui_sweep4/5`: `(tid, True|False, detail)` -- a **bool**;
* `ui_sweep6`: `(chapter, stem, status, detail)` with a 4th `SKIP` bucket.

That is a vacuous pass waiting to happen. `ui_sweep4.py`'s footer counted
failures as `fails = [r for r in RESULTS if not r[1]]` -- and a non-empty
string like `"FAIL"` is truthy, so the moment any sweep's string
convention met that footer, a real failure reported as **zero** failures
and the wrapper went green.

So there is now ONE schema: `(tid, ok, detail)` where

* `ok=True`  -- passed
* `ok=False` -- failed
* `ok=None`  -- XFAIL, a documented engine gap: neither a pass nor a fail

and `ok` must actually be a bool (or None). `Recorder.record` refuses a
`"PASS"`/`"FAIL"` string outright -- that is the whole point. The old
convention was never wrong *arithmetically*; it was a different type that
a truthiness test silently mis-read.

`Recorder.skip()` is separate on purpose: SKIP is not a result (nothing
was checked), so it must not be able to masquerade as one.

This module imports neither PySide6 nor raptor at module scope, so
`tests/integration/test_sweep_base.py` can unit-test the `Recorder`
without standing up a `QApplication` -- or, worse, globally patching the
Qt classes for the rest of the pytest session.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

#: The XFAIL marker in every recorded `ok`. Named so a sweep writes
#: `XFAIL` at the call site instead of a bare `None` that reads like "I
#: forgot to pass ok".
XFAIL = None


@dataclass(frozen=True)
class Result:
    """One recorded check. `ok` is True / False / None (XFAIL)."""

    tid: Any
    ok: bool | None
    detail: str = ""


@dataclass
class Recorder:
    """Collects check results and prints the ONE summary line the wrapper
    regex (`test_ui_sweeps.TOTAL_RE`) reads.

    `ok` is validated at record time rather than at tally time: a
    wrong-typed status must fail loudly where it is written, not silently
    count as a pass three hundred lines later.
    """

    results: list[Result] = field(default_factory=list)
    skipped: list[tuple[Any, str]] = field(default_factory=list)

    # ------------------------------------------------------------ writing

    @staticmethod
    def _check_ok(tid: Any, ok: bool | None) -> bool | None:
        if ok is None:
            return XFAIL
        if not isinstance(ok, bool):
            # Where the old `(tid, "PASS"|"FAIL", detail)` convention dies:
            # a truthiness-based `not r[1]` test cannot tell "FAIL" from
            # True. Refuse the string rather than record a lie.
            raise TypeError(
                f"result for {tid!r}: ok must be True, False or None "
                f"(None = XFAIL), got {ok!r} of type "
                f"{type(ok).__name__}. Do not pass 'PASS'/'FAIL' strings "
                f"-- use a bool, and None for a documented engine gap."
            )
        return ok

    def record(self, tid: Any, ok: bool | None, detail: str = "") -> Result:
        result = Result(tid=tid, ok=self._check_ok(tid, ok), detail=detail)
        self.results.append(result)
        status = "XFAIL" if result.ok is XFAIL else (
            "PASS" if result.ok else "FAIL")
        tail = detail.splitlines()[-1] if detail else "ok"
        print(f"[{status}] {tid}: {tail}")
        return result

    def skip(self, tid: Any, reason: str = "") -> None:
        """Not a result: nothing was checked (e.g. an exercise-starter
        file whose section is empty by design)."""
        self.skipped.append((tid, reason))

    def run_check(self, tid: Any, fn) -> None:
        """Run `fn`, recording pass/fail with its traceback.

        Guarded: a check that records its own result (a softer assertion
        than an exception -- "expected some rows hidden, got none" can be
        reported without blowing up the rest of the sweep) keeps that
        result. Without the guard, sweeps 1/4/5's unconditional
        `record(tid, True)` after `fn()` returned silently overwrote the
        failure the check had just recorded for itself.
        """
        try:
            fn()
        except Exception:
            self.record(tid, False, traceback.format_exc())
            return
        if not any(r.tid == tid for r in self.results):
            self.record(tid, True)

    # ------------------------------------------------------------ tallying

    @property
    def passes(self) -> list[Result]:
        return [r for r in self.results if r.ok is True]

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.ok is False]

    @property
    def xfails(self) -> list[Result]:
        return [r for r in self.results if r.ok is XFAIL]

    # ------------------------------------------------------------ reporting

    def report(self, *, msgboxes: list[tuple[str, str]] = (),
               stream=None) -> int:
        """Print the summary and return the process exit code.

        The `TOTAL: N passed, M failed` line is the contract with
        `tests/integration/test_ui_sweeps.py`: exactly one, in exactly
        that shape, so the wrapper needs one regex instead of two
        dialects. XFAIL and SKIP are reported but never counted as either
        a pass or a failure.
        """
        out = sys.stdout if stream is None else stream
        print("\n" + "=" * 70, file=out)
        for r in self.failures:
            print(f"FAIL {r.tid}\n{r.detail}\n", file=out)
        for r in self.xfails:
            print(f"XFAIL {r.tid}: {r.detail}", file=out)
        for tid, reason in self.skipped:
            print(f"SKIP {tid}: {reason}", file=out)
        if msgboxes:
            print("Modal message boxes captured during run:", file=out)
            for kind, text in msgboxes:
                print(f"  QMessageBox.{kind}: {text}", file=out)
        print(f"\nTOTAL: {len(self.passes)} passed, "
              f"{len(self.failures)} failed", file=out)
        if self.xfails or self.skipped:
            print(f"({len(self.xfails)} xfail(engine gaps), "
                  f"{len(self.skipped)} skipped)", file=out)
        return 1 if self.failures else 0


# --------------------------------------------------------------- Qt stubs


def install_modal_stubs(*,
                        preflight_run_anyway: bool = False,
                        capture_information: bool = True,
                        capture_limit: int | None = 120,
                        ) -> list[tuple[str, str]]:
    """Neutralise every modal the app can raise, and return the list the
    captured message boxes are appended to.

    A headless offscreen run has nobody to answer a dialog, so an
    unpatched `exec()` blocks forever and an unpatched `question()`
    picks an arbitrary button. All of them are patched here, once, for
    all six sweeps:

    * `QDialog.exec` -> Accepted. `QMessageBox.exec` is not defined on
      QMessageBox itself, so it resolves through the MRO to
      `QDialog.exec` and is neutralised by that one patch.
    * `QFileDialog.exec` -> Accepted (`QFileDialog.DialogCode.Accepted`
      is the same value).
    * `QMessageBox.question` -> Discard, so an unsaved-changes prompt a
      check deliberately left dirty resolves to "discard".
    * `dialogs.question_box` -> False. This is the confirm seam the
      dialogs actually call (see `raptor/gui/ui/dialogs.py`); patching
      only `QMessageBox.question` leaves a real modal behind.
    * `QMessageBox.critical/warning/information` are captured, not shown.
      An unexpected `critical` is a real finding, so the captures are
      returned rather than swallowed.

    Two per-sweep deviations, both load-bearing:

    * `preflight_run_anyway=True` additionally answers the "Unsupported
      Model Features" preflight box with its *Run Anyway* button
      (`MainWindow._run_simulation` checks `box.clickedButton() is
      run_btn`). Only `ui_sweep6.py` wants that. The default is the
      opposite: with `exec` stubbed, a real `clickedButton()` is always
      None, so preflight issues cancel the run -- which is what sweeps
      1-5 have always exercised.
    * `capture_information=False` for `ui_sweep6.py`, which classifies a
      chapter from the captured text (an empty run with a message naming
      a known engine gap becomes XFAIL). An informational box leaking
      into that list would change a chapter's verdict.
    """
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

    import raptor.gui.ui.dialogs as _dialogs

    QDialog.exec = lambda self: QDialog.Accepted
    QFileDialog.exec = lambda self: QFileDialog.DialogCode.Accepted
    QMessageBox.question = lambda *a, **k: QMessageBox.Discard
    # question_box is the confirm seam the dialogs use (see dialogs.py) --
    # stub it too, or a headless confirm (e.g. discard-edits on close)
    # hangs on a real modal.
    _dialogs.question_box = lambda *a, **kw: False

    captured: list[tuple[str, str]] = []

    def _capture(kind):
        def inner(*a, **k):
            text = a[2] if len(a) > 2 else k.get("text", "")
            text = str(text)
            captured.append((kind, text if capture_limit is None
                             else text[:capture_limit]))
            return QMessageBox.StandardButton.Ok
        return inner

    QMessageBox.critical = _capture("critical")
    QMessageBox.warning = _capture("warning")
    if capture_information:
        QMessageBox.information = _capture("information")

    if preflight_run_anyway:
        # 'Run Anyway' path: the preflight box is a QMessageBox whose exec
        # must return so the run proceeds, and whose clickedButton must
        # read back as the first (Run Anyway) button.
        QMessageBox.exec = staticmethod(lambda self=None: 0)
        QMessageBox.clickedButton = lambda self: (
            self.buttons()[0] if hasattr(self, "buttons") and self.buttons()
            else None)

    return captured


# ------------------------------------------------------------ sweep context


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def sweep_tmp() -> str:
    """The sweep's scratch directory, created if it does not exist.

    `RAPTOR_SWEEP_TMP` is what the wrapper points at its pytest
    `tmp_path`, so one sweep's autosave/recovery copies never reach the
    real temp dir, where a later `MainWindow` would find them and prompt.
    """
    path = os.environ.get("RAPTOR_SWEEP_TMP") or str(
        Path(tempfile.gettempdir()) / "raptor_sweep")
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class SweepContext:
    """The one `QApplication` + `MainWindow` + golden dir a sweep drives.

    `load(name, path=None)` is the spelling every sweep had re-derived:
    open a golden fixture through the real UI and hand back the diagram
    view.
    """

    app: Any
    window: Any
    golden: str
    tmp: str
    recorder: Recorder
    msgboxes: list[tuple[str, str]] = field(default_factory=list)

    # --- app plumbing -------------------------------------------------

    def pe(self) -> None:
        """`processEvents` under the name 5 of the 6 scripts used."""
        self.app.processEvents()

    @property
    def view(self):
        return self.window._diagram_dock.view

    def load(self, name: str, path: str | None = None):
        from raptor.core.fileio.rbd import load_rbd

        p = path or f"{self.golden}/{name}/input.rbd"
        self.window._load_into_app(load_rbd(p), path or f"{name}.rbd")
        self.app.processEvents()
        return self.window._diagram_dock.view

    # --- results ------------------------------------------------------

    def record(self, tid, ok, detail="") -> Result:
        return self.recorder.record(tid, ok, detail)

    def run_check(self, tid, fn) -> None:
        self.recorder.run_check(tid, fn)

    def skip(self, tid, reason="") -> None:
        self.recorder.skip(tid, reason)

    def report(self, stream=None) -> int:
        return self.recorder.report(msgboxes=self.msgboxes, stream=stream)


def make_context(*,
                 preflight_run_anyway: bool = False,
                 capture_information: bool = True,
                 capture_limit: int | None = 120,
                 resize: tuple[int, int] = (1600, 1000),
                 need_tmp: bool = True,
                 setup_window: Callable[[Any], None] | None = None,
                 ) -> SweepContext:
    """Standard sweep preamble: modal stubs -> QApplication -> MainWindow.

    `setup_window(window)` runs after the window is constructed but
    *before* it is shown, for a sweep that has to patch the window
    itself (`ui_sweep6.py` replaces `_save_file`, so the sweep cannot
    write back the tutorial models it is only reading).

    `__file__`-relative, so it works the same whether the sweep was run
    through `_run_sweep.py` (the exec guard `test_ui_sweeps.py` uses) or
    directly from a shell.
    """
    from PySide6.QtWidgets import QApplication

    from raptor.gui.ui.main_window import MainWindow

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    msgboxes = install_modal_stubs(
        preflight_run_anyway=preflight_run_anyway,
        capture_information=capture_information,
        capture_limit=capture_limit)
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    if setup_window is not None:
        setup_window(window)
    window.resize(*resize)
    window.show()
    app.processEvents()
    return SweepContext(
        app=app,
        window=window,
        golden=str(repo_root() / "tests" / "golden"),
        tmp=sweep_tmp() if need_tmp else "",
        recorder=Recorder(),
        msgboxes=msgboxes,
    )
"""Shared fixtures: redirect QSettings to a temp file, reap stale Qt
windows between GUI tests, and install a watchdog that dumps all thread
stacks if a test runs past its timeout."""
import faulthandler
import os
import tempfile
import weakref
import zlib

import pytest

# Tests read inspector fields on any tab, so build every tab on selection (the application builds only the shown one).
os.environ.setdefault("RAPTOR_INSPECTOR_EAGER_TABS", "1")
# Headless unless asked otherwise. Run from a desktop terminal, Qt picked
# the real display: windows flashed up on screen, and the window manager
# would not make a test's window active, so every test that needs an
# active window (key tips, shortcuts) failed. Set QT_QPA_PLATFORM to watch.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow

from raptor.gui.resources.design_tokens import ThemeManager
from raptor.gui.ui.diagram import DiagramView

# The UI sweep scripts are standalone programs (they run at import time);
# they are only ever launched by tests/integration/test_ui_sweeps.py in a
# subprocess, never collected by pytest itself.
collect_ignore_glob = ["integration/sweeps/*"]

#: Per-test watchdog (seconds) for tests not marked `slow`. The watchdog
#: exists to catch hangs, not slow tests, so it only has to exceed the
#: slowest *legitimate* test with margin -- and that margin used to be
#: 0.3 s, which is not margin.
#:
#: Measured 2026-10-03 on the full engine tier at `-n 6`: the slowest test
#: is `test_golden_fidelity::test_final_results_fidelity[TriggerWakeOrder]`
#: at **59.7 s**, against a 60 s budget. At that ratio the default tier does
#: not complete: **15 of 3,225 tests hard-killed their xdist worker** at
#: `faulthandler` process exit -- a death, not a skip or an xfail -- and 40
#: more landed between 30 s and 60 s. A GitHub runner is several times
#: slower than a workstation, so the set of tests that get killed grows
#: with the machine rather than with the code.
#:
#: 300 s is ~5x the slowest legitimate test, which is the margin a
#: slower-than-workstation runner needs. The cost is honest and worth
#: stating: a genuinely hung test now takes 5 minutes to be reported
#: instead of 1. With xdist that is one worker, not the run.
#:
#: Override with `RAPTOR_TEST_TIMEOUT`; the coverage job in
#: `tools/engine_coverage.py` sets 1800, because coverage roughly
#: triples run time. `slow`-marked tests keep their own budget below.
_TEST_TIMEOUT = float(os.environ.get("RAPTOR_TEST_TIMEOUT", "300"))


@pytest.fixture(scope="session", autouse=True)
def _isolated_qsettings(tmp_path_factory):
    """Redirect every `QSettings()` construction for the test session to a
    fresh temp `.ini` file.

    Tests construct `QApplication`/`MainWindow` directly and never call
    `raptor.gui.app`'s own `setApplicationName`, so `QSettings()` falls
    back to Qt's default: a file named after `sys.argv[0]`'s basename in
    the system-wide `~/.config/Unknown Organization/`. Because pytest
    gives every test file in one command the same `sys.argv[0]`, they
    share one `.conf` file, and a test that writes settings can clobber
    the next test's. Redirecting to a temp store fixes that for any test
    that reads or writes QSettings. Existing stray files in the user's
    real `~/.config` are left alone."""
    tmp_dir = tmp_path_factory.mktemp("qsettings")
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_dir))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)



@pytest.fixture(autouse=True)
def _open_models_in_new_windows():
    """`files/open_in` back to its default ("new") after each test: a test
    of replacing a model in the same window sets "this", and settings are
    shared by the whole session."""
    yield
    QSettings().remove("files/open_in")


#: The real `MainWindow._recovery_path`, kept before the session patch below
#: so a test of the per-window file naming can call it.
_ORIGINAL_RECOVERY_PATH: list = []


@pytest.fixture
def original_recovery_path():
    """The unpatched `MainWindow._recovery_path` (a function of `self`)."""
    return _ORIGINAL_RECOVERY_PATH[0]


@pytest.fixture(scope="session", autouse=True)
def _isolated_recovery_copy(tmp_path_factory):
    """Point `MainWindow`'s crash-recovery copy at this session's own temp
    directory.

    The real path is one fixed file under the system temp directory. A new
    `MainWindow` (and `app.main()`, through the splash) checks it 600 ms
    after start-up and, if it exists, asks whether to open it with a modal
    `question_box` that nobody answers headless. The test then hangs until
    the watchdog kills its worker. The file is left behind by the UI
    sweeps' autosave checks, by a GUI session that crashed, or by another
    xdist worker, so which test hung depended on timing (a different one
    most runs). A per-session path cannot be seen by any of those."""
    from raptor.gui.ui.main_window import MainWindow
    path = str(tmp_path_factory.mktemp("recovery") / "unsaved_recovery.rbds")
    _ORIGINAL_RECOVERY_PATH.append(MainWindow._recovery_path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(MainWindow, "_recovery_path", lambda self: path)
        yield

#: Every `DiagramView` built during the session (weakly held).
_views: "weakref.WeakSet[DiagramView]" = weakref.WeakSet()
_view_init = DiagramView.__init__


def _registering_init(self, *args, **kwargs):
    _view_init(self, *args, **kwargs)
    _views.add(self)


DiagramView.__init__ = _registering_init


def _clear_orphan_undo_stacks():
    """Empty the undo stack of every parentless `DiagramView` a test left
    behind.

    Such a view is freed by the cycle collector, not by Qt. When the
    collector tears down a view whose `QUndoStack` still holds Python
    `QUndoCommand`s, `~QUndoStack` deletes commands whose Python side is
    already gone, and the process segfaults inside `gc.collect()` --
    wherever the next full collection happens (Hypothesis runs one per
    test). Clearing the stack while the view is still alive deletes the
    commands safely. Views inside a window are deleted by `_reap`
    instead, in Qt's own order, which is safe."""
    for view in list(_views):
        try:
            if view.parent() is None:
                view._stack.clear()
        except RuntimeError:  # the C++ view is already gone
            pass


def _reap():
    """Delete stale top-level windows and force their pending deletion.

    `deleteLater()` followed by a plain `processEvents()` does not drain
    `DeferredDelete` events under `offscreen`, so every `MainWindow` a
    test constructs stays alive for the session and keeps its
    tooltip-reshow timer installed. `QApplication.sendPostedEvents(None,
    QEvent.Type.DeferredDelete)` runs those queued deletions immediately;
    `topLevelWidgets()` then drops to 0 after each reap and construction
    time stays flat. The existing `QMainWindow`/`QDockWidget`-only filter is
    sufficient because their child popups and menus cascade-delete with
    them once the deletion runs."""
    for tlw in QApplication.topLevelWidgets():
        if isinstance(tlw, (QMainWindow, QDockWidget)):
            tlw.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


class UnansweredModal(AssertionError):
    """A GUI test opened a modal dialog that nothing answered."""


#: How long a modal `exec()` may stay open before the test is failed.
#: Generous for a dialog that closes itself via a zero-length timer; short
#: next to the watchdog.
_MODAL_GRACE_MS = 3000

#: Blocking convenience dialogs. They run their own C++ event loop, so a
#: Python-level `exec` patch cannot see them, and they cannot be timed out
#: from here: an unpatched call is refused outright.
_BLOCKING_STATICS = (
    ("QMessageBox", ("critical", "warning", "information", "question",
                     "about", "aboutQt")),
    ("QFileDialog", ("getOpenFileName", "getOpenFileNames",
                     "getSaveFileName", "getExistingDirectory")),
    ("QInputDialog", ("getText", "getInt", "getDouble", "getItem",
                      "getMultiLineText")),
    ("QColorDialog", ("getColor",)),
    ("QFontDialog", ("getFont",)),
)


@pytest.fixture(scope="module", autouse=True)
def _no_unanswered_modals_in_module_setup(request):
    """The same guard, for a GUI module's module- or class-scoped fixtures.

    Those are set up before any function-scoped fixture, so the per-test
    guard below is not yet in place while they run, and a message box one
    opens waits for a click that never comes. That hung the whole suite once
    (`test_collapse_characterization.py`'s module fixture opened a real
    `QMessageBox`). An autouse fixture runs before the other fixtures of its
    scope, so this one is in place first; a session-scoped fixture is still
    unguarded and should not open dialogs."""
    if not _is_gui_module(str(request.path)):
        yield
        return
    mp = pytest.MonkeyPatch()
    try:
        _install_modal_guard(mp)
        yield
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def _no_unanswered_modals(request, monkeypatch):
    """Fail a GUI test that opens a modal nothing answers, with the
    dialog's title -- instead of hanging until the watchdog `os._exit`s
    the whole run with no pytest summary.

    That is exactly how a broken Export PDF (its error box opens modally)
    silently ended every full GUI run at ~69 % for weeks: the next test
    blocked, the watchdog killed the process, and pytest reported nothing.

    * `QDialog.exec` (shared by QMessageBox, QFileDialog, ...) runs
      normally, but a timer rejects it after `_MODAL_GRACE_MS` and the
      test then fails. A dialog that closes itself promptly is
      unaffected.
    * The blocking static helpers raise immediately.

    Any test that expects a dialog patches these itself; `monkeypatch`
    applies that AFTER this fixture, so it wins."""
    if request.node.get_closest_marker("gui") is None:
        yield
        return
    _install_modal_guard(monkeypatch)
    yield


def _install_modal_guard(monkeypatch) -> None:
    """Patch `QDialog.exec` with a timeout and refuse the blocking statics
    (see `_no_unanswered_modals`)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog
    import PySide6.QtWidgets as qtw

    real_exec = QDialog.exec

    def guarded_exec(self, *args, **kwargs):
        fired: list[bool] = []

        def _timeout():
            fired.append(True)
            try:
                self.reject()
            except RuntimeError:
                pass

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(_timeout)
        timer.start(_MODAL_GRACE_MS)
        try:
            result = real_exec(self, *args, **kwargs)
        finally:
            timer.stop()
        if fired:
            text = getattr(self, "text", lambda: "")()
            raise UnansweredModal(
                f"{type(self).__name__} {self.windowTitle()!r} was still "
                f"open after {_MODAL_GRACE_MS} ms and nothing answered it "
                f"({text[:120]!r}). Patch it in the test, or fix the code "
                f"path that opened it.")
        return result

    monkeypatch.setattr(QDialog, "exec", guarded_exec)

    def refuse(cls_name, fn_name):
        def _refuse(*args, **kwargs):
            title = next((a for a in args if isinstance(a, str)), "")
            raise UnansweredModal(
                f"{cls_name}.{fn_name}({title!r}) would block forever "
                f"in a headless test. Patch it in the test, or fix the "
                f"code path that opened it.")
        return staticmethod(_refuse)

    for cls_name, names in _BLOCKING_STATICS:
        cls = getattr(qtw, cls_name)
        for fn_name in names:
            if hasattr(cls, fn_name):
                monkeypatch.setattr(cls, fn_name, refuse(cls_name, fn_name))


@pytest.fixture(autouse=True)
def _test_hygiene(request):
    # Qt window reaping is only meaningful for tests that can create windows; skipping it for
    # engine tests removes a `processEvents()` round-trip from ~1,000 tests.
    is_gui = request.node.get_closest_marker("gui") is not None
    if is_gui:
        _reap()
    # Watchdog: dump all thread stacks if a test runs past its timeout. A
    # `slow`-marked test legitimately runs much longer (the UI sweeps use a
    # real subprocess.run(..., timeout=600)), so give it enough room for its
    # own timeout to fire first and produce a normal pytest failure rather
    # than a hard process exit.
    is_slow = request.node.get_closest_marker("slow") is not None
    # Use tempfile.gettempdir(): it always exists, unlike a fixed
    # /tmp/opencode path that was only present on one developer's box.
    # One file per xdist worker: every test reopens it, so a shared file
    # would be wiped by another worker right after a watchdog kill.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    timeout_fd = open(os.path.join(tempfile.gettempdir(), f"pytest_hang{worker}.dump"), "w")
    faulthandler.dump_traceback_later(
        650 if is_slow else _TEST_TIMEOUT, exit=True, file=timeout_fd)
    yield
    faulthandler.cancel_dump_traceback_later()
    timeout_fd.close()
    if is_gui:
        ThemeManager.instance()._callbacks.clear()
        _clear_orphan_undo_stacks()
        _reap()



# --- automatic `gui` marker -------------------------------------------------------------
# Engine work needs fast feedback, so GUI tests must be excludable: `pytest -m "not gui"`.
# A module is a GUI module if it imports PySide6 or anything under `raptor.gui`, or lives in
# tests/integration (subprocess offscreen MainWindow sweeps). Derived from the module's own
# imports rather than hand-tagged, so a new test is classified correctly without anyone
# remembering a marker. Passing `-m` on the command line replaces `addopts`' own
# `-m "not slow and not fidelity"`, so use `-m "not gui and not slow and not fidelity and not long"`.
import re as _re

_GUI_IMPORT = _re.compile(r"^\s*(?:from|import)\s+(?:PySide6|raptor\.gui)\b", _re.M)
_gui_module_cache: dict[str, bool] = {}


def _is_gui_module(path: str) -> bool:
    hit = _gui_module_cache.get(path)
    if hit is None:
        norm = path.replace(os.sep, "/")
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            text = ""
        hit = "/tests/integration/" in norm or bool(_GUI_IMPORT.search(text))
        _gui_module_cache[path] = hit
    return hit


def pytest_collection_modifyitems(config, items):
    for item in items:
        if _is_gui_module(str(item.path)):
            item.add_marker(pytest.mark.gui)
        if _is_fidelity_module(str(item.path)):
            item.add_marker(pytest.mark.fidelity)
    _keep_this_shard(config, items)


# --- CI sharding ------------------------------------------------------------------------
# `RAPTOR_TEST_SHARD=k/N` keeps the tests whose id hashes to slot k of N, so N
# CI jobs run disjoint parts of a tier in parallel and together run all of it.
# By test, not by file: the fidelity tier is a handful of files at 45-90 s a
# test, and splitting whole files would leave one shard with nearly all of it.
# crc32 of the node id is the same in every process, so xdist workers (which
# each collect) agree on the shard. Unset, nothing is deselected.
def _keep_this_shard(config, items):
    spec = os.environ.get("RAPTOR_TEST_SHARD", "")
    if not spec:
        return
    k, n = (int(part) for part in spec.split("/"))
    if not 1 <= k <= n:
        raise pytest.UsageError(f"RAPTOR_TEST_SHARD={spec!r}: need 1 <= k <= N")
    keep, drop = [], []
    for item in items:
        (keep if zlib.crc32(item.nodeid.encode()) % n == k - 1 else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


# --- automatic `fidelity` marker -------------------------------------------------------
# The eight whole-output comparison suites. They are the reason the default run
# cannot finish: measured on this tree at `-n 6`, they hold 1,030 of the 5,499
# default-selected tests (19%) and cost 14m52s of a 19m11s run -- about 78% of
# the CI test job's wall clock. Individual tests run 45-90 s (`input_40_runs`,
# `LadL8A`, `TriggerWakeOrder`), which is what the per-test watchdog in this file
# was raised to 300 s to survive.
#
# `slow` already means something else and cannot be reused: it marks the six
# UI sweeps, and re-using it would make "slow" mean two unrelated things. These
# are marked `fidelity` instead, and `addopts` excludes both.
#
# **This set is an explicit list on purpose.** The `gui` marker above is derived
# from a module's imports, because "is this a GUI test" is a property of the
# code. "Is this expensive" is not: it was measured, and it changes as the
# corpus and the engine do. A heuristic here would silently reclassify a suite
# on an unrelated edit, which is the failure mode this whole change exists to
# remove. Measured per-file cost, 2026-10-05, -n 6:
#
#   test_engine_snapshot.py          89.5s / 82.0s / 59.7s / 45.6s / 41.1s per test
#   test_write_back_characterization  80.7s in *setup* alone
#   test_event_log_fidelity.py       76.0s / 55.9s / 54.1s
#   test_golden_fidelity.py          72.2s / 50.6s / 49.2s
#   test_sd_diff_sweep.py            53.0s
#   test_reports.py                  42.2s
#   test_random_draw_count.py        30.9s / 30.8s / 26.9s
#   test_plot_report_fidelity.py, test_probe_fidelity_captures.py
#
# What is NOT lost, stated plainly because it is the whole risk of this change:
# these are the tests that compare a whole run's output against a file real
# Raptor wrote. `tests/behaviour/` now pins every transition the corpus exhibits
# (263 of 263) and 0 of 43 engine modules fall below their recorded floor without
# them, but it does not compare reports. So they still run -- on every push to
# `main`, nightly, and on demand -- and `main` is not protected by them until
# that push lands. See `.github/workflows/tests.yml`'s `fidelity` job.
_FIDELITY_MODULES = frozenset({
    "tests/unit/test_engine_snapshot.py",
    "tests/unit/test_event_log_fidelity.py",
    "tests/unit/test_golden_fidelity.py",
    "tests/unit/test_plot_report_fidelity.py",
    "tests/unit/test_probe_fidelity_captures.py",
    "tests/unit/test_random_draw_count.py",
    "tests/unit/test_reports.py",
    "tests/unit/test_sd_diff_sweep.py",
    "tests/unit/test_write_back_characterization.py",
})
_fidelity_module_cache: dict[str, bool] = {}


def _is_fidelity_module(path: str) -> bool:
    hit = _fidelity_module_cache.get(path)
    if hit is None:
        norm = path.replace(os.sep, "/")
        i = norm.find("/tests/")
        rel = norm[i + 1:] if i >= 0 else norm
        hit = rel in _FIDELITY_MODULES
        _fidelity_module_cache[path] = hit
    return hit


_exit_status = None


def pytest_sessionfinish(session, exitstatus):
    """Record pytest's own exit status for `pytest_unconfigure` below.

    This deliberately does not call `os._exit` itself. The terminal
    reporter's `pytest_sessionfinish` is a `hookimpl(wrapper=True)`: plain
    `pytest_sessionfinish` hooks (this one included) run during that
    wrapper's yield, and the wrapper's post-yield code prints the final
    "X passed in Ys" summary only after every plain hook returns. An
    `os._exit` here would therefore kill the process before the summary is
    written, regardless of stdio flushing or hook ordering."""
    global _exit_status
    _exit_status = int(exitstatus)


def pytest_unconfigure(config):
    """Force a clean process exit with pytest's own status.

    Fires after `pytest_sessionfinish` (including the terminal reporter's
    summary line) during pytest's config-teardown phase. PySide6's static
    destruction intermittently segfaults after every test has passed, and
    the crash is teardown-only, so exit deterministically instead of
    letting the interpreter walk Qt's destroyed globals."""
    import sys
    if _exit_status is None:
        # pytest_sessionfinish never ran (e.g. a collection-time crash
        # before any session existed) -- nothing to force-exit around.
        return
    if os.environ.get("RAPTOR_NO_HARD_EXIT"):
        return
    if sys.gettrace() is not None:
        # A sys.settrace()-based debugger/tracer is active -- os._exit
        # would skip its atexit data flush, silently losing measurements
        return
    if getattr(sys, "monitoring", None) is not None and sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        # coverage.py on Python 3.12+ uses sys.monitoring (PEP 669), not
        # sys.settrace(), so it is invisible to the gettrace() check above
        # and os._exit would silently drop the whole .coverage file.
        return
    # `os._exit` skips the interpreter's own normal-shutdown stdio flush,
    # so flush explicitly first; this matters whenever stdout is not a TTY,
    # where Python fully-buffers instead of line-buffering.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)

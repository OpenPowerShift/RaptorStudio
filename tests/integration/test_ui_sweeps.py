"""Integration wrapper: runs the UI smoke sweeps in
tests/integration/sweeps/ui_sweep*.py as subprocesses and fails if any
reports failures.

Marked `slow` so the normal unit run stays fast:
    pytest tests/integration -m slow
or explicitly:
    pytest tests/integration/test_ui_sweeps.py

The sweeps stub modal dialogs and drive the real MainWindow offscreen;
they are the closest thing to end-to-end coverage this project has.

Every sweep records its results through the one `Recorder` in
`tests/integration/sweeps/_sweep_base.py` and prints the one
`TOTAL: N passed, M failed` line matched below, so this regex is the
whole contract -- there is no second report dialect to fall back on.
XFAIL (a documented engine gap) and SKIP are reported by the sweep but
counted as neither a pass nor a failure, which is what makes an XFAIL
strictness regression visible here rather than silently green.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("SKIP_UI_SWEEPS") == "1",
                       reason="UI sweeps skipped via SKIP_UI_SWEEPS=1"),
]

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
RUNNER = pathlib.Path(__file__).resolve().parent / "_run_sweep.py"
SWEEPS = [REPO / "tests" / "integration" / "sweeps" / "ui_sweep1.py",
          REPO / "tests" / "integration" / "sweeps" / "ui_sweep2.py",
          REPO / "tests" / "integration" / "sweeps" / "ui_sweep3.py",
          REPO / "tests" / "integration" / "sweeps" / "ui_sweep4.py",
          REPO / "tests" / "integration" / "sweeps" / "ui_sweep5.py",
          REPO / "tests" / "integration" / "sweeps" / "ui_sweep6.py"]

#: The one line every sweep prints via `Recorder.report()`.
TOTAL_RE = re.compile(r"TOTAL:\s*(\d+) passed,\s*(\d+) failed")


@pytest.mark.parametrize("script", SWEEPS, ids=lambda p: p.name)
def test_ui_sweep(script: pathlib.Path, tmp_path: pathlib.Path):
    assert script.exists(), f"missing sweep script {script}"
    if script.name == "ui_sweep6.py" and not (REPO / "legacy" / "rbd").is_dir():
        pytest.skip("ui_sweep6 walks the local-only, git-ignored legacy/rbd/ "
                    "sample models (see legacy/README.md)")
    # Each sweep gets its own temp directory, so what it writes there
    # (including the autosave checks' crash-recovery copy) never reaches
    # the real one, where a later MainWindow would find it and prompt.
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", TMPDIR=str(tmp_path))
    # 1200s, not 600. Measured on an idle machine: ui_sweep6 takes 696s
    # before this D-15 consolidation and 903s after -- both over a 600s
    # budget, so the wrapper was failing on the wall clock rather than on
    # anything the sweeps reported. Both runs produce identical results
    # (74 checks, 59 pass, 0 fail, 15 skip), which is how the two were
    # compared. The headroom is for a loaded CI box; a genuinely hung
    # sweep still fails, just later.
    proc = subprocess.run(
        [sys.executable, str(RUNNER), str(script)],
        capture_output=True, text=True, timeout=1200,
        cwd=str(REPO), env=env,
    )
    # Exactly one TOTAL line per sweep (`Recorder.report()`); a second
    # one would mean two tally paths, which is the bug this file exists
    # to make impossible.
    found = TOTAL_RE.findall(proc.stdout)
    if not found:
        # A crash before the TOTAL line is itself a failure — surface it.
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
        pytest.fail(f"{script.name} crashed before printing TOTAL:\n{tail}")
    assert len(found) == 1, (
        f"{script.name} printed {len(found)} TOTAL lines ({found}); the "
        f"sweeps must report through exactly one Recorder.report() call"
    )
    passed, failed = (int(g) for g in found[0])
    if failed:
        # Recorder.report() prints a "FAIL <name>" block per failing
        # check; the chapter sweep (6) additionally marks it inline as
        # "   [!] name" in its per-chapter breakdown.
        fail_lines = [l for l in proc.stdout.splitlines()
                      if l.startswith("FAIL") or l.lstrip().startswith("[!]")]
        pytest.fail(f"{script.name}: {failed} check(s) failed:\n"
                    + "\n".join(fail_lines[:10])
                    + (f"\n(exit={proc.returncode})" if proc.returncode else ""))
    assert passed > 0, f"{script.name} reported no checks"

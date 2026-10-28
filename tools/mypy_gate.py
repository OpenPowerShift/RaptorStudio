#!/usr/bin/env python3
"""CI type-check gate.

* Everything outside `raptor/gui/` must be mypy-clean.
* `raptor/gui/` carries legacy errors (mostly PySide6 short enum names such
  as `Qt.NoPen` and unchecked Optional Qt returns). Each file's error count
  may not rise above `tools/mypy_gui_baseline.json`; when a file
  improves, the gate asks for the baseline to be lowered so the gain sticks.

    python tools/mypy_gate.py            # check (CI)
    python tools/mypy_gate.py --update   # rewrite the GUI baseline
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("mypy_gui_baseline.json")
_ERROR = re.compile(r"^(raptor/[^:]+):\d+: error:")


def run_mypy() -> list[str]:
    proc = subprocess.run([sys.executable, "-m", "mypy", "--no-color-output", "--no-error-summary"],
                          cwd=REPO, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"mypy crashed (exit {proc.returncode})")
    return [line for line in proc.stdout.splitlines() if _ERROR.match(line)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="rewrite the GUI baseline from this run")
    args = ap.parse_args()

    errors = run_mypy()
    core = [e for e in errors if not e.startswith("raptor/gui/")]
    gui = Counter(_ERROR.match(e).group(1) for e in errors if e.startswith("raptor/gui/"))  # type: ignore[union-attr]

    if args.update:
        BASELINE.write_text(json.dumps(dict(sorted(gui.items())), indent=2) + "\n")
        print(f"GUI baseline: {sum(gui.values())} errors in {len(gui)} files")
        return 0

    failed = False
    if core:
        failed = True
        print(f"{len(core)} mypy error(s) outside raptor/gui (must be zero):")
        print("\n".join(core))

    baseline: dict[str, int] = json.loads(BASELINE.read_text())
    worse = {f: (baseline.get(f, 0), n) for f, n in gui.items() if n > baseline.get(f, 0)}
    better = {f: (n, gui.get(f, 0)) for f, n in baseline.items() if gui.get(f, 0) < n}
    if worse:
        failed = True
        print("GUI files with MORE mypy errors than the baseline:")
        for f, (old, new) in sorted(worse.items()):
            print(f"  {f}: {old} -> {new}")
        for e in errors:
            m = _ERROR.match(e)
            if m and m.group(1) in worse:
                print("   ", e)
    if better:
        failed = True
        print("GUI files with FEWER errors -- lock in the gain with "
              "`python tools/mypy_gate.py --update`:")
        for f, (old, new) in sorted(better.items()):
            print(f"  {f}: {old} -> {new}")

    print(f"mypy gate: {len(core)} non-GUI errors; GUI {sum(gui.values())} "
          f"(baseline {sum(baseline.values())})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

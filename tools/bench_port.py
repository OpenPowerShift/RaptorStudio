#!/usr/bin/env python3
"""Speed of the engine on a few golden models: wall-clock milliseconds per run, one worker and several.

    python tools/bench_port.py [--repeat 2] [--jobs 1,4,8]

Run it on an otherwise idle machine (the pytest tiers and a live desktop session both skew it). The model is
loaded once and a warm-up run is discarded; each figure is the best of --repeat batches. Prints a Markdown table
for the README's benchmark section.
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from raptor.core.engine.runner import run_simulation  # noqa: E402
from raptor.core.fileio.rbd import load_rbd  # noqa: E402

#: (fixture, runs per timed batch, what it is)
MODELS = (
    ("FactoryDemo", 40, "5 blocks, 8,760 h"),
    ("HierarchyExample", 100, "13 blocks, hierarchy"),
    ("Phasing3", 10, "16 blocks, phased mission, 2 events"),
    ("LadL6Full", 20, "20 blocks, pools, resources, PM, dependencies"),
)


def _cpu() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _time(rbd, runs: int, jobs: int, repeat: int) -> float:
    best = float("inf")
    for _ in range(repeat):
        t = time.perf_counter()
        run_simulation(rbd, num_runs=runs, num_workers=jobs)
        best = min(best, time.perf_counter() - t)
    return best


def _cli_seconds(name: str, runs: int, repeat: int) -> float:
    """End to end through `raptor run`: interpreter start-up, the model's own analyses (weak link, cost,
    capacity where the model enables them) and the result summary, one worker."""
    best = float("inf")
    for _ in range(repeat):
        t = time.perf_counter()
        subprocess.run([sys.executable, "-m", "raptor.cli.main", "run",
                        str(ROOT / "tests" / "golden" / name / "input.rbd"), "-n", str(runs), "-f", "none"],
                       cwd=ROOT, check=True, capture_output=True)
        best = min(best, time.perf_counter() - t)
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--jobs", default="1,4,8")
    args = ap.parse_args()
    jobs = [int(j) for j in args.jobs.split(",")]
    print(f"CPU: {_cpu()}; Python {platform.python_version()}; {platform.system()}\n")
    print("| Model | Runs | " + " | ".join(f"-j {j}: s (ms/run)" for j in jobs) + " |")
    print("|---|---|" + "---|" * len(jobs))
    for name, runs, what in MODELS:
        rbd = load_rbd(str(ROOT / "tests" / "golden" / name / "input.rbd"))
        run_simulation(rbd, num_runs=1)                      # warm-up
        cells = []
        for j in jobs:
            s = _time(rbd, runs, j, args.repeat)
            cells.append(f"{s:.2f} ({1000 * s / runs:.0f})")
        print(f"| {name} ({what}) | {runs} | " + " | ".join(cells) + " |")
    print("\nEnd to end through `raptor run` (one worker, includes interpreter start-up, the model's analyses and "
          "the summary):\n")
    print("| Model | Runs | s (ms/run) |\n|---|---|---|")
    for name, runs, _what in MODELS:
        s = _cli_seconds(name, runs, args.repeat)
        print(f"| {name} | {runs} | {s:.2f} ({1000 * s / runs:.0f}) |")


if __name__ == "__main__":
    main()

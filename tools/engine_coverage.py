#!/usr/bin/env python3
"""Engine branch coverage: per module, per function, with CI floors.

    python tools/engine_coverage.py run [--check-floors] [--html DIR]
    python tools/engine_coverage.py report cov.json [--check-floors]
    python tools/engine_coverage.py report cov.json --update-floors

`run` executes the engine tier (`-m "not slow and not gui"`) under branch
coverage with per-test contexts (so `coverage html --show-contexts` answers
"which test covers this line?"), then prints the module table and the
largest and worst-covered functions. `--check-floors` fails if any engine
module's branch coverage drops below `coverage_floors.json`; after coverage
improves, `--update-floors` raises the floors so the gain is kept.

Needs `pytest-cov` and `pytest-xdist` (the `dev` extra). Coverage roughly
triples run time, and a GitHub runner is several times slower again, so `run`
sets `RAPTOR_TEST_TIMEOUT` to 1800 s for the per-test watchdog in
tests/conftest.py. The slowest test takes about 100 s under coverage locally;
at 600 s the watchdog killed an xdist worker on CI, and the partial data
that left behind failed every floor.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FLOORS = Path(__file__).with_name("coverage_floors.json")
ENGINE_PREFIXES = ("raptor/core/engine/", "raptor/core/scheduler.py", "raptor/core/rng.py")


def _functions(path: Path):
    """Yield (qualified_name, first_line, last_line) for every def."""
    tree = ast.parse(path.read_text())

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                if not isinstance(child, ast.ClassDef):
                    yield name, child.lineno, child.end_lineno
                yield from walk(child, name + ".")

    yield from walk(tree, "")


def _engine_files(cov_json: Path) -> dict[str, dict]:
    data = json.loads(cov_json.read_text())["files"]
    return {k.replace(os.sep, "/"): v for k, v in data.items()
            if any(p in k.replace(os.sep, "/") for p in ENGINE_PREFIXES)
            and v["summary"]["num_statements"]}


def branch_percent(summary: dict) -> float:
    total = summary["num_branches"]
    return 100.0 * (total - summary["missing_branches"]) / total if total else 100.0


def report(cov_json: Path, worst: int, biggest: int) -> None:
    engine = _engine_files(cov_json)
    print(f"{'module':44s} {'stmts':>6s} {'miss':>5s} {'br':>5s} {'brmiss':>6s} {'line%':>6s} {'br%':>6s}")
    for path, d in sorted(engine.items()):
        s = d["summary"]
        rel = path.split("raptor/", 1)[-1]
        print(f"{rel:44s} {s['num_statements']:6d} {s['missing_lines']:5d} {s['num_branches']:5d} "
              f"{s['missing_branches']:6d} {100 * s['covered_lines'] / s['num_statements']:5.1f}% "
              f"{branch_percent(s):5.1f}%")

    rows = []
    for path, d in engine.items():
        executed, missing = set(d["executed_lines"]), set(d["missing_lines"])
        br_hit, br_miss = d.get("executed_branches", []), d.get("missing_branches", [])
        for name, lo, hi in _functions(REPO / path):
            span = set(range(lo, hi + 1))
            stmts = len((executed | missing) & span)
            if not stmts:
                continue
            miss = len(missing & span)
            branch_total = sum(lo <= a <= hi for a, _ in br_hit) + sum(lo <= a <= hi for a, _ in br_miss)
            bm = sum(lo <= a <= hi for a, _ in br_miss)
            rows.append((path.split("raptor/", 1)[-1], name, lo, stmts, miss, branch_total, bm))

    def show(title, selected):
        print(f"\n{title}")
        for f, n, lo, st, m, branch_total, bm in selected:
            print(f"  {f}:{lo:<5d} {n:48s} stmts={st:4d} line={100 * (st - m) / st:5.1f}% "
                  f"branch={100 * (branch_total - bm) / max(branch_total, 1):5.1f}% ({bm}/{branch_total} branches missed)")

    show(f"Largest {biggest} functions", sorted(rows, key=lambda r: -r[3])[:biggest])
    show(f"Worst-covered {worst} functions (>= 8 statements)",
         sorted((r for r in rows if r[3] >= 8), key=lambda r: (r[3] - r[4]) / r[3])[:worst])


def check_floors(cov_json: Path) -> bool:
    floors: dict[str, float] = json.loads(FLOORS.read_text())
    got = {p: branch_percent(d["summary"]) for p, d in _engine_files(cov_json).items()}
    ok = True
    for path, floor in sorted(floors.items()):
        pct = got.get(path)
        if pct is None:
            print(f"FLOOR: {path} has a floor but no coverage data")
            ok = False
        elif pct < floor:
            print(f"FLOOR: {path} branch coverage {pct:.1f}% is below its floor {floor:.1f}%")
            ok = False
    missing = sorted(set(got) - set(floors))
    if missing:
        print("FLOOR: modules without a floor (run --update-floors): " + ", ".join(missing))
        ok = False
    print("coverage floors: " + ("OK" if ok else "FAILED"))
    return ok


def update_floors(cov_json: Path) -> None:
    got = {p: math.floor(branch_percent(d["summary"]) * 10) / 10 for p, d in _engine_files(cov_json).items()}
    FLOORS.write_text(json.dumps(dict(sorted(got.items())), indent=2) + "\n")
    print(f"wrote {len(got)} floors to {FLOORS.relative_to(REPO)}")


def run(workers: int, out: Path, html: Path | None) -> tuple[int, Path]:
    out.mkdir(parents=True, exist_ok=True)
    data_file = out / ".coverage"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", COVERAGE_FILE=str(data_file),
               RAPTOR_TEST_TIMEOUT=os.environ.get("RAPTOR_TEST_TIMEOUT", "1800"))
    cmd = [sys.executable, "-m", "pytest", "-q", "-m", "not slow and not gui",
           "-p", "no:cacheprovider", "-n", str(workers),
           "--cov=raptor.core", "--cov-branch", "--cov-context=test", "--cov-report="]
    rc = subprocess.call(cmd, cwd=REPO, env=env)
    cov_json = out / "cov.json"
    subprocess.check_call([sys.executable, "-m", "coverage", "json", "-q",
                           f"--data-file={data_file}", "-o", str(cov_json)], cwd=REPO)
    if html is not None:
        subprocess.check_call([sys.executable, "-m", "coverage", "html", "-q", "--show-contexts",
                               f"--data-file={data_file}", "-d", str(html)], cwd=REPO)
    return rc, cov_json


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "raptor_engine_cov")
    r.add_argument("--html", type=Path, default=None, help="also write an HTML report here")
    p = sub.add_parser("report")
    p.add_argument("cov_json", type=Path)
    p.add_argument("--update-floors", action="store_true")
    for sp in (r, p):
        sp.add_argument("--check-floors", action="store_true")
        sp.add_argument("--worst", type=int, default=30)
        sp.add_argument("--biggest", type=int, default=25)
    a = ap.parse_args()

    rc = 0
    if a.cmd == "run":
        rc, cov_json = run(a.workers, a.out, a.html)
    else:
        cov_json = a.cov_json
        if a.update_floors:
            update_floors(cov_json)
            return 0
    report(cov_json, a.worst, a.biggest)
    if a.check_floors and not check_floors(cov_json):
        rc = rc or 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

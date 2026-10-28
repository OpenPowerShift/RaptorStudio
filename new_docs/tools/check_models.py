"""Headless check: load every tutorial model, run it, print key numbers.

Sniffs each file's real format (JSON vs legacy text) instead of trusting
its extension: gen_models.py's write() always emits .rbds (JSON) content
no matter what filename it is given, so extension alone is not a reliable
format signal. Every model file is walked and dispatched on its sniffed
content.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import load_rbds
from raptor.core.engine.runner import run_simulation_with_reports

MODELS = os.path.join(os.path.dirname(__file__), "..", "models")


def load_any(path):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        first_char = fh.read(1)
    return load_rbds(path) if first_char == "{" else load_rbd(path)


def all_model_names():
    return sorted(n for n in os.listdir(MODELS) if n.endswith((".rbd", ".rbds")))


def check_one(name, verbose=True, runs=None):
    """Load and run one model. Returns None if it loaded and ran, else
    a one-line failure message -- split out so a single parametrized
    pytest case (one model each, a second or so) stays well under this
    suite's 25s per-test watchdog instead of one ~20s-for-everything
    test (see tests/conftest.py).

    `runs` overrides the file's own `NumRuns`. It defaults to `None`,
    meaning "use the shipped count", which is what `main()` and the
    command line have always done; it exists so a caller that only
    wants to know whether a model *loads and runs* (rather than what it
    reports over its shipped trial count) does not pay for 2000 trials.
    """
    path = os.path.join(MODELS, name)
    try:
        f = load_any(path)
        if runs is None:
            runs = int(float(f.sections["SYSTEM_DATA"].rows[0]["NumRuns"]))
        # `collect_logs=False`: this function reports on the run tables,
        # not the event logs, and the report is built from the per-run
        # report rows rather than the logs. Verified bit-identical.
        logs, eor, final = run_simulation_with_reports(f, runs, collect_logs=False)
    except Exception as exc:  # noqa: BLE001 -- report, don't crash the batch
        return f"{name}: {exc}"
    if verbose:
        print("=" * 70)
        print(name, "runs:", len(logs))
        for ln in final:
            if any(k in ln for k in ("Availability", "MTBF", "Reliability",
                                     "Mean", "Fail", "System")):
                print("  " + ln.rstrip())
    return None


def main():
    names = all_model_names()
    failures = [msg for msg in (check_one(n) for n in names) if msg]

    print("=" * 70)
    if failures:
        print(f"check_models.py: {len(failures)} of {len(names)} failed to load/run:")
        for line in failures:
            print(" -", line)
        return 1
    print(f"check_models.py: OK -- all {len(names)} model(s) in models/ loaded and ran.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

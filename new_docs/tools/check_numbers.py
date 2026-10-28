"""Quality gate: re-run every registered model, fail if a chapter's
quoted headline number has drifted outside its stated tolerance.

Every tutorial model in this book uses a fixed RNG seed, so a re-run is
bit-for-bit deterministic; the tolerance below exists only to cover the
book's own "about" rounding in prose, not sampling variance. A number
that drifts outside tolerance means either the engine changed behaviour
or the model file changed without updating the prose that quotes it.

The registry covers every model with one unambiguous headline metric at
the file's own default run count. Not covered, because each needs a
non-default run or config to reproduce the chapter's comparison:
CarWash1 (Availability and Dependability read together), Laundromat1
(naive vs 2200h-delayed-stats comparison), Water1/Water2 (units-produced /
Grand_Total cost extraction).

Usage (from repo root): .venv/bin/python new_docs/tools/check_numbers.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MODELS = os.path.join(os.path.dirname(__file__), "..", "models")

# (model file, metric name in the SUMMARY table, expected value as a
#  fraction, absolute tolerance, run-count override or None to use the
#  file's own shipped NumRuns, source note)
#
# The run-count override matters: several tutorial files ship with a
# small trial count (a teaching default) while README.md's quoted number
# uses a larger count -- the general rule stated at the top of README's
# table is 2000 trials unless a row states otherwise in parentheses.
# Using the shipped count where README documents a different count would
# fail on files that have not drifted at all.
#
# HOW MANY TRIALS EACH ROW ACTUALLY NEEDS. The override is the row's
# *gate*, not the book's own trial count: what it has to do is detect a
# drift in the reported mean, and the mean's own sampling error is what
# decides how many trials that takes. An entry may use fewer trials than
# README quotes (or more) without weakening the gate, because the gate
# compares `mean` to `expected +/- tol`, and the quantity that decides
# whether a real drift is visible is
#
#     SEM(N) = StDev / sqrt(N)          vs    tol
#
# The rows are split into two kinds, and the split is the whole point:
#
# * **Availability** rows average a continuous per-run ratio (uptime over
#   a year, etc.), so `St.Dev` is small and the mean converges fast. For
#   these, a handful of trials already puts the SEM an order of magnitude
#   inside the tolerance, so the trial count is set by "enough to keep the
#   noise out of the way", and the gate is as sharp as the tolerance
#   allows. Counts below were chosen by measurement: the smallest N on
#   the ladder {2,5,10,20,40,...} with SEM <= tol/8 and at least 2 SEM of
#   headroom left inside the band.
# * **Reliability** rows are per-run 0/1 (did the system survive the
#   mission?), so `St.Dev` is ~0.5 and the mean converges like
#   0.5/sqrt(N). These are genuinely noise-dominated at any affordable N
#   -- even at 2000 trials the SEM is over half the tolerance -- so they
#   keep the 2000-trial figure. Lowering them would not make the gate
#   faster *and* still sharp; it would only make it a coin flip.
#
# So the cheap rows got cheaper and the expensive rows are honest about
# why they did not. `None` still means "use the file's own NumRuns".
REGISTRY = [
    ("Lantern1.rbds", "Reliability", 0.38, 0.02, 2000, "README: reliability @48h"),
    ("Espresso1.rbds", "Reliability", 0.37, 0.02, 2000, "README: reliability @500h"),
    ("EspressoDep1.rbds", "Reliability", 0.21, 0.02, 2000, "README: reliability @500h"),
    ("Drone1.rbds", "Reliability", 0.59, 0.02, 2000, "README: reliability @48h"),
    ("FinaleShow1.rbds", "Reliability", 0.94208, 0.02, 2000, "README: analytic 0.94208"),
    ("Toaster_Fixed.rbds", "Reliability", 1.00, 0.001, None, "README: reliability @600h"),
    ("Toaster_Expo.rbds", "Reliability", 0.55, 0.02, 2000, "README: reliability @600h"),
    ("Toaster_Weibull.rbds", "Reliability", 0.67, 0.02, 2000, "README: reliability @600h"),
    ("Vendo1.rbds", "Availability", 0.97, 0.02, 10, "README: availability (year)"),
    ("TacoTruck1.rbds", "Availability", 0.63, 0.02, 20, "README: availability @1000h (20 runs)"),
    ("Bakery1.rbds", "Availability", 0.997, 0.01, 10, "README: availability @720h (20 runs)"),
    ("CarCare0.rbds", "Availability", 0.990, 0.01, 10, "README: availability @2000h (200 runs)"),
    ("CarCare1.rbds", "Availability", 0.992, 0.01, 10, "README: availability @2000h (200 runs)"),
    ("Van1.rbds", "Availability", 0.987, 0.01, 10, "README: availability @2000h (200 runs)"),
    ("VanFlat.rbds", "Availability", 0.987, 0.01, 10, "README: availability @2000h (200 runs)"),
    ("DC1.rbds", "Availability", 0.99948, 0.002, 10, "README: 99.948% down/yr (200 runs)"),
    ("DC2.rbds", "Availability", 0.99984, 0.002, 10, "README: 99.984% down/yr (200 runs)"),
    ("ProdLine0.rbds", "Availability", 0.935, 0.02, 10, "README: ~93.5% (ships at 200 runs)"),
    ("ProdLine1.rbds", "Availability", 0.965, 0.02, 10, "README: ~96.5% (ships at 200 runs)"),
    ("Flight1.rbds", "Reliability", 0.8215, 0.02, None, "README/PLAN: ~82% (ships at 2000 runs)"),
]


def run_summary(path, runs_override=None):
    """Load and run a model (native .rbds or legacy .rbd), return
    {metric_name: mean_float} parsed from the Final_Results_Report's
    ***SUMMARY*** table. A censored mean (">1.23") has its ">" stripped
    -- comparisons against a censored value are a lower bound, not an
    exact figure, but still catch a value that has moved far away."""
    from raptor.core.engine.runner import run_simulation_with_reports
    from raptor.core.fileio.rbds import load_rbds
    from raptor.core.fileio.rbd import load_rbd
    # gen_models.py's write() always emits .rbds (JSON) content, no
    # matter what filename it is given -- every tutorial model here is
    # JSON regardless of extension, so sniff the real format rather than
    # trusting the extension.
    with open(path, encoding="utf-8", errors="ignore") as fh:
        first_char = fh.read(1)
    f = load_rbds(path) if first_char == "{" else load_rbd(path)
    runs = runs_override or int(float(f.sections["SYSTEM_DATA"].rows[0]["NumRuns"]))
    # `collect_logs=False`: this function reads only the report tables,
    # and the Final_Results_Report is built from the per-run report rows,
    # not from the event logs. Verified bit-identical output (see
    # tests/unit/test_docs_quality_gates.py) -- it only stops the runner
    # retaining a list of log lines nothing here looks at, which is the
    # bulk of the cost on the log-heavy models.
    _logs, _eor, final = run_simulation_with_reports(f, runs, collect_logs=False)

    header_idx = next(i for i, ln in enumerate(final) if ln.strip().startswith("Minimum"))
    cols = final[header_idx].split()  # ["Minimum", "Mean", "Maximum", "St.Dev", "SEM"]
    # +1: a header line is JUST the column labels, but each data row
    # below it starts with an extra leading row-name token the header
    # doesn't have -- "Reliability 0.0 0.3775 1.0 ..." -- so the row's
    # numeric fields are offset one position past where they'd land if
    # you naively used the header's own column index.
    mean_col = cols.index("Mean") + 1

    summary = {}
    for ln in final[header_idx + 1:]:
        stripped = ln.strip()
        if not stripped:
            break
        parts = stripped.split()
        name = parts[0].split("(")[0]  # "MDT(357-runs)" -> "MDT"
        try:
            summary[name] = float(parts[mean_col].lstrip(">"))
        except (IndexError, ValueError):
            continue
    return summary


def check_one(entry):
    """Run one REGISTRY entry. Returns None if it matched, else a
    one-line failure message -- the unit run a single parametrized
    pytest case can assert on directly (each takes a few seconds, not
    the whole registry's ~1 minute, so it comfortably fits under this
    suite's 25s per-test watchdog -- see tests/conftest.py)."""
    fname, metric, expected, tol, runs_override, note = entry
    path = os.path.join(MODELS, fname)
    if not os.path.isfile(path):
        return f"{fname}: file not found (registry entry stale?)"
    summary = run_summary(path, runs_override)
    actual = summary.get(metric)
    if actual is None:
        return f"{fname}: no {metric} row in its SUMMARY table"
    if abs(actual - expected) > tol:
        return (f"{fname}: {metric} = {actual:.4f}, expected {expected:.4f} "
                f"+/- {tol} ({note}) -- drifted by {abs(actual - expected):.4f}")
    return None


def main():
    failures = [msg for msg in (check_one(entry) for entry in REGISTRY) if msg]

    if failures:
        print(f"check_numbers.py: {len(failures)} of {len(REGISTRY)} drifted:\n")
        for line in failures:
            print(" -", line)
        return 1

    print(f"check_numbers.py: OK -- {len(REGISTRY)} model(s) matched their "
          f"quoted numbers within tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

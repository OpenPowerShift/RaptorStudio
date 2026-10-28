#!/usr/bin/env bash
# Fast feedback for ENGINE work: run the tests without the GUI ones.
#
#   tools/test_engine.sh [tier] [extra pytest args...]
#
# Tiers (sweep / ordering / engine TIMED 2026-09-20 on this machine; `full` is from an earlier run):
#   sweep     ~67s   the RNG draw-order sweep only (tests/unit/test_sd_diff_sweep.py). The most
#                    sensitive single signal for same-instant / scheduling changes: it caught
#                    most regressions during the ER3-29 work. BLIND SPOTS (caught only by the
#                    other tiers): event-LOG order with no RNG draw involved (`SimStartProbe2`),
#                    report-level drift, and step-session/GUI-facing behaviour (the FEV test).
#                    A green `sweep` is therefore necessary, not sufficient.
#   ordering  ~2.3min sweep + golden fidelity (3 report axes) + orchestrator event logs + the
#                    lifecycle/step-session/tie-probe tests. Run this before committing an
#                    ordering change.
#   engine    ~4.5min (DEFAULT) every non-GUI test: 693 tests (672 pass + 21 xfail), vs ~29 min full.
#   full      ~29min everything, GUI included. Run once before pushing a change that touches the
#                    engine's public behaviour (GUI tests do exercise engine output).
#
# WHY IT IS DONE THIS WAY: tests/conftest.py marks a test module `gui` AUTOMATICALLY when it
# imports PySide6 or raptor.gui (or lives under tests/integration), so a NEW test is classified
# correctly with nobody remembering a marker. Note `-m` on the command line REPLACES the
# `-m "not slow"` in pyproject's addopts, hence the explicit "not gui and not slow" below.
#
# Caveat: ~578 GUI tests are excluded from every tier except `full`. They construct MainWindow
# and widgets, so they are not engine tests, but a change to engine OUTPUT (event-log text,
# report values) can still break a GUI test. Run `full` before pushing.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
TIER=${1:-engine}; [ $# -gt 0 ] && shift
PY="$REPO/.venv/bin/python -m pytest -q -p no:randomly"
case "$TIER" in
  sweep)    exec $PY tests/unit/test_sd_diff_sweep.py "$@" ;;
  ordering) exec $PY tests/unit/test_sd_diff_sweep.py tests/unit/test_golden_fidelity.py \
                     tests/unit/test_orchestrator.py tests/unit/test_events_engine.py \
                     tests/unit/test_hierarchy.py tests/unit/test_block_lifecycle.py \
                     tests/unit/test_step_session.py tests/unit/test_tie_order_probe.py \
                     tests/unit/test_dependency.py tests/unit/test_cold_standby.py "$@" ;;
  engine)   exec $PY tests -m "not gui and not slow" "$@" ;;
  full)     exec $PY tests "$@" ;;
  *) echo "unknown tier '$TIER' (sweep | ordering | engine | full)" >&2; exit 2 ;;
esac

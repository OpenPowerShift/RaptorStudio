"""List every kind of event-log line the engine writes, from the golden fixtures.

    python tools/event_vocabulary.py [--runs 2] [--limit N]

Prints `type | action | comment pattern | count` (numbers in comments are
replaced by `#`). The downtime-attribution observer is written against this
table; a line kind missing from its state machine is a bug there.
"""
from __future__ import annotations

import argparse
import collections
import glob
import re
import signal
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def pattern(comment: str) -> str:
    return _NUMBER.sub("#", comment)


class _Timeout(Exception):
    pass


def vocabulary(runs: int = 2, limit: int | None = None, seconds: int = 20):
    from raptor.core.engine.eventlog import LogLine
    from raptor.core.engine.runner import run_simulation
    from raptor.core.fileio.rbd import load_rbd

    def alarm(*_):
        raise _Timeout

    signal.signal(signal.SIGALRM, alarm)
    counts: collections.Counter = collections.Counter()
    skipped = []
    paths = sorted(glob.glob(str(REPO / "tests" / "golden" / "*" / "input.rbd")))[:limit]
    for path in paths:
        signal.alarm(seconds)
        try:
            logs = run_simulation(load_rbd(path), runs)
        except _Timeout:
            skipped.append((path, "timeout"))
            continue
        except Exception as exc:  # noqa: BLE001 - a fixture the engine refuses is not vocabulary
            skipped.append((path, type(exc).__name__))
            continue
        finally:
            signal.alarm(0)
        for log in logs:
            for line in log:
                if isinstance(line, LogLine):
                    counts[(line.type, line.action, pattern(line.comment))] += 1
    return counts, skipped, len(paths)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    counts, skipped, n = vocabulary(args.runs, args.limit)
    print(f"# {n} fixtures, {args.runs} runs each, {len(skipped)} skipped")
    for (kind, action, comment), count in sorted(counts.items()):
        print(f"{kind} | {action} | {comment} | {count}")
    for path, why in skipped:
        print(f"# skipped {Path(path).parent.name}: {why}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

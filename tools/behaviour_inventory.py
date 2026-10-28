#!/usr/bin/env python3
"""The behavioural denominator: every state transition real Raptor performed.

The behaviour suite needs a denominator for
"complete behaviour coverage" that is not branch coverage, because branch
coverage can be 100% and the output still wrong. The corpus already contains
one: every event-log capture is tab-separated `(time, object_type, name,
state, detail)`, so the set of distinct `(object_type, from -> to)` rows the
real engine wrote is mechanically extractable. Measured over the 206
captures that is **318** transitions (Block 195, Node 49, System 36, Hier 17,
Event 17, Trigger 2, Pool 2) -- the documented number, and the
denominator `tests/behaviour/test_b_behaviour_coverage.py` ratchets against.

This script is the *extractor*; the gate is the assertion. It reads inert
recorded text and runs nothing (rule B0), and it deliberately knows nothing
about classification -- that lives in `tests/behaviour/behaviour_manifest.toml`
so that a judgement call is reviewable data rather than tool logic.

Definition, stated exactly because the count depends on it:

* a **data row** is a line whose first column parses as a simulation time.
  That admits every `0.000000  Block  A  Good  InitializeStress=1.0` row and
  rejects the `Event_Log for <model>.rbd <date>` header and prose lines such
  as `Started Gathering Statistics` -- the header's second column is the word
  `for`, which is why counting "every row with a tab" gives 319, not 318;
* the transitions of a capture are read in **file order**, and `from` is the
  state last logged for that same `(object_type, name)`, or `--` for its first
  appearance. State is *not* reset at a `Begin_Run_N` boundary: Raptor does
  not re-announce every object at a run start, so resetting would invent 16
  `-- -> ...` rows that the engine never wrote (302 instead of 318).

Usage (repo root):

    python tools/behaviour_inventory.py --report      # the summary
    python tools/behaviour_inventory.py --write       # regenerate the inventory
    python tools/behaviour_inventory.py --report -j 1 # no worker pool

Reads ~520 MB across the 206 captures; about 12 s single-threaded, 8 s with
the default pool, so the gate can afford to re-derive the denominator on
every run rather than trusting a committed copy of it.
"""
from __future__ import annotations

import argparse
import gzip
import multiprocessing
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden"
CAPTURES = REPO / "docs" / "engine-design" / "behaviour-transitions.toml"

#: `rpt_events1.txt`, `rpt_events_frobnicate.txt`, `... .txt.gz` -- but not
#: `rpt_events.txt.bak` and not a report that merely starts with the name.
_CAPTURE_NAME = re.compile(r"^rpt_events.*\.(txt|txt\.gz)$")

#: The state column's "there is no previous state" marker. Raptor writes it
#: literally, so the inventory writes it literally too rather than inventing
#: a spelling of its own.
NONE = "--"

#: The documented measurement, stated here so a drift in the corpus is
#: visible in `--report` instead of silently redefining "complete".
DOCUMENTED = {
    "Block": 200,
    "Node": 49,
    "System": 37,
    "Hier": 17,
    "Event": 17,
    "Trigger": 2,
    "Pool": 2,
}
DOCUMENTED_TOTAL = 324

#: How many example captures to keep per transition, so a gate failure can
#: name where the behaviour came from rather than just what it was.
EXAMPLES = 3


class Transition(NamedTuple):
    """One `(object_type, from -> to)` the corpus exhibits."""

    object_type: str
    from_state: str
    to_state: str

    def __str__(self) -> str:
        return f"{self.object_type} {self.from_state} -> {self.to_state}"


class Inventory(NamedTuple):
    """The whole corpus, reduced to what the gate needs."""

    transitions: dict[Transition, list[str]]
    """Every distinct transition, with up to `EXAMPLES` capture paths that
    exhibited it -- repo-relative, so a gate failure can name the file the
    behaviour came out of rather than only naming the behaviour."""

    captures: list[str]
    """Every capture read, `tests/golden/<fixture>/legacy/<file>`. 206 today."""

    by_type: dict[str, int]
    """Distinct transitions per object type -- the documented table."""

    rows: int
    """Data rows read across all captures (5,932,390 today)."""


def fixture_of(capture: str) -> str:
    """`tests/golden/<fixture>/legacy/<file>` -> `<fixture>`."""
    return capture.split("/")[2] if capture.count("/") > 2 else capture


def capture_paths(root: Path = GOLDEN) -> list[Path]:
    """Every legacy event-log capture, `.txt` and `.txt.gz`, sorted."""
    return sorted(
        p
        for p in root.glob("*/legacy/rpt_events*")
        if p.is_file() and _CAPTURE_NAME.match(p.name)
    )


def _open(path: Path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def scan_capture(path: Path) -> tuple[set[Transition], int]:
    """Transitions in one capture, plus the number of data rows in it.

    Bytes, not text: the captures are 520 MB of latin-1 with CRLF endings and
    the columns are space-padded, so the whole file can be split and compared
    as `bytes` and only decoded once per *distinct* value. Splitting on the
    first four tabs (`split(b"\\t", 4)`) leaves the detail column alone.
    """
    seen: set[Transition] = set()
    last: dict[tuple[bytes, bytes], bytes] = {}
    rows = 0
    with _open(path) as fh:
        for line in fh:
            if b"\t" not in line:
                continue  # blank line, or prose such as `Started Gathering Statistics`
            fields = line.split(b"\t", 4)
            if len(fields) < 4:
                continue
            try:
                float(fields[0])
            except ValueError:
                continue  # the `Event_Log for <model>.rbd <date>` header
            obj_type = fields[1].strip()
            key = (obj_type, fields[2].strip())
            to_state = fields[3].strip()
            seen.add(
                Transition(
                    obj_type.decode("latin-1"),
                    last.get(key, NONE.encode()).decode("latin-1"),
                    to_state.decode("latin-1"),
                )
            )
            last[key] = to_state
            rows += 1
    return seen, rows


def collect(workers: int = 0, root: Path = GOLDEN) -> Inventory:
    """Scan every capture and merge the per-capture transition sets."""
    paths = capture_paths(root)
    if not paths:
        raise SystemExit(f"no rpt_events* captures under {root}")
    if workers == 0:
        workers = min(8, multiprocessing.cpu_count())
    if workers <= 1:
        found = [scan_capture(p) for p in paths]
    else:
        # `spawn`, not `fork`: CPython 3.14 warns that forking a
        # multi-threaded process may deadlock, and the test runner has Qt's
        # threads alive when the gate runs from pytest. The workers only read
        # files and return small sets, so the interpreter restart costs about
        # 50 ms each and buys the certainty. `map`, not submit-and-wait, or
        # the pool would be decorative.
        with ProcessPoolExecutor(
            workers, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            found = list(pool.map(scan_capture, paths, chunksize=4))
    merged: dict[Transition, list[str]] = defaultdict(list)
    rows = 0
    for path, (seen, n_rows) in zip(paths, found):
        capture = str(path.relative_to(REPO))
        rows += n_rows
        for transition in seen:
            examples = merged[transition]
            if capture not in examples and len(examples) < EXAMPLES:
                examples.append(capture)
    by_type: Counter[str] = Counter(t.object_type for t in merged)
    return Inventory(dict(merged), [str(p.relative_to(REPO)) for p in paths],
                     dict(by_type), rows)


def agreement(by_type: dict[str, int], total: int) -> str:
    """One line saying whether the documented measurement still holds."""
    got = {k: by_type.get(k, 0) for k in DOCUMENTED}
    if total == DOCUMENTED_TOTAL and got == DOCUMENTED:
        return (
            f"matches the documented count "
            f"({', '.join(f'{k} {v}' for k, v in DOCUMENTED.items())}; total "
            f"{DOCUMENTED_TOTAL})"
        )
    drift = {k: (DOCUMENTED[k], by_type.get(k, 0)) for k in DOCUMENTED if by_type.get(k, 0) != DOCUMENTED[k]}
    extra = sorted(set(by_type) - set(DOCUMENTED))
    return (
        f"DIFFERS from the documented count: total {total} (documented {DOCUMENTED_TOTAL}), "
        f"per-type drift {drift or 'none'}"
        + (f", unlisted object types {extra}" if extra else "")
    )


def render_report(inventory: Inventory) -> str:
    """The `--cov-report`-style summary: a denominator and its breakdown."""
    lines = [
        f"captures: {len(inventory.captures)}",
        f"data rows: {inventory.rows:,}",
        f"distinct transitions: {len(inventory.transitions)}",
        agreement(inventory.by_type, len(inventory.transitions)),
        "",
        "[cols=\"1,1,1,5\",options=\"header\"]",
        "|===",
        "| Object type | Transitions | Example |",
    ]
    for object_type in sorted(inventory.by_type, key=lambda t: (-inventory.by_type[t], t)):
        sample = next(
            t for t in sorted(inventory.transitions) if t.object_type == object_type
        )
        lines.append(
            f"| {object_type} | {inventory.by_type[object_type]} "
            f"| `{sample}` (from {fixture_of(inventory.transitions[sample][0])}) |"
        )
    lines += ["|===", ""]
    return "\n".join(lines)


def render_inventory(inventory: Inventory) -> str:
    """The machine-readable form, same shape as the manifest it is checked against."""
    lines = [
        "# The behavioural denominator, generated by",
        "# tools/behaviour_inventory.py --write. Do not edit by hand.",
        "#",
        "# Every distinct `(object_type, from -> to)` row real Raptor wrote across",
        "# the legacy event-log captures, with the capture each was seen in.",
        "# tests/behaviour/behaviour_manifest.toml classifies these; this file only",
        "# measures them, and is regenerated whenever the corpus changes.",
        "",
        "[inventory]",
        f'captures = {len(inventory.captures)}',
        f'rows = {inventory.rows}',
        f'transitions = {len(inventory.transitions)}',
    ]
    for object_type in sorted(inventory.by_type):
        lines.append(f'{object_type} = {inventory.by_type[object_type]}')
    for transition in sorted(inventory.transitions):
        lines += [
            "",
            "[[transition]]",
            f'object = "{transition.object_type}"',
            f'from_state = "{transition.from_state}"',
            f'to_state = "{transition.to_state}"',
            "examples = ["
            + ", ".join(f'"{name}"' for name in inventory.transitions[transition])
            + "]",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--report", action="store_true", help="print the summary")
    mode.add_argument("--write", action="store_true", help="regenerate the inventory")
    ap.add_argument(
        "-o", "--out", type=Path, default=CAPTURES, help=f"default: {CAPTURES.relative_to(REPO)}"
    )
    ap.add_argument("-j", "--workers", type=int, default=0, help="0 = min(8, cpus)")
    a = ap.parse_args()
    inventory = collect(a.workers)
    if a.write:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(render_inventory(inventory), encoding="utf-8")
        print(f"wrote {a.out}: {len(inventory.transitions)} transitions from "
              f"{len(inventory.captures)} captures")
    if a.report or not a.write:
        print(render_report(inventory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
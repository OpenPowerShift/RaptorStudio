"""The retirement gate: is the legacy corpus still load-bearing?

The 610 MB of event-log
captures under `tests/golden/*/legacy/` are currently the oracle for the whole
engine tier. They should stop being the oracle -- not be deleted, but demoted
to a regression net -- and the only thing that may authorise that is this gate,
transition by transition. A green run here is what
"the project has stopped needing Raptor" means.

**The denominator.** Every capture is tab-separated
`(time, object_type, name, state, detail)`, so the set of distinct
`(object_type, from -> to)` rows real Raptor actually wrote is extractable.
`tools/behaviour_inventory.py` extracts it -- **318** of them, and
this module re-derives that number on every run from the captures themselves
rather than trusting a committed copy, because a gate that reads its own
denominator from a file it also writes cannot notice the file rotting.

**How "asserted by some test" is decided.** Deliberately *not* by running the
tests. Every `assert_timeline(run, [...])` literal in `tests/behaviour/*.py`
is parsed with `ast` and its `(object_type, from, to)` triples collected. Two
consequences, both wanted: there is no artefact to go stale, and a test that
merely *mentions* a transition in a docstring or a comment cannot satisfy the
gate. The accepted literal shapes, all three of which are the same claim:

    assert_timeline(run, [(12.0, "Block:A", "Good", "Failed"), ...])
    assert_timeline(run, [(12.0, "Node", "join", "Good", "Degraded"), ...])
    assert_timeline(run, [(12.0, "A", "Good", "Failed"), ...])   # a bare block name

A row the parser cannot read is reported as a failure rather than skipped: a
gate that silently cannot see half the suite is worse than no gate.

**The ratchet.** `tests/behaviour/behaviour_manifest.toml` classifies all 318
as `pinned` / `planned` / `unreachable` / `artefact`, and carries two committed
baselines. The gate fails if

* a corpus transition is unclassified, or a classified transition is no
  longer in the corpus (a stale entry is as wrong as a missing one);
* a `pinned` claim has no literal behind it -- the manifest cannot flatter
  itself;
* an `unreachable` or `artefact` entry has no reason;
* a `planned`/`pinned` entry names a file that is not in the rung
  catalogue;
* either ratchet number went *down*. That last one is what makes this a
  ratchet rather than a snapshot: retiring a behaviour is not a thing you can
  do quietly, and coverage that regresses is a red build even if every test
  still passes.

The committed baseline is not zero and not a stub: the `ast` pass finds **16**
corpus transitions already asserted by the ladder's S1-S3 spine literals in
`test_b_ladder.py`, and those are the entries marked `pinned`. Raising the
number is the ordinary way to make this gate green *more* strongly; lowering
it is what turns the gate red.

`pytest -s`, or `python tests/behaviour/test_b_behaviour_coverage.py
--cov-report`, prints the count. It only goes up.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import pytest

REPO = Path(__file__).resolve().parents[2]
BEHAVIOUR = REPO / "tests" / "behaviour"
MANIFEST = BEHAVIOUR / "behaviour_manifest.toml"

sys.path.insert(0, str(REPO / "tools"))
import behaviour_inventory as inv  # noqa: E402

#: The test files of the rung catalogue. A manifest
#: entry that claims a behaviour will be pinned "by test_b_whatever.py" has to
#: name one of these, so a reference cannot rot into a wish for a file that
#: was never going to exist.
CATALOGUE_FILES = frozenset(
    {
        # 5.1 -- S0, pure functions
        "test_b_rng.py",
        "test_b_diststats.py",
        "test_b_draws.py",
        "test_b_scheduler.py",
        "test_b_states.py",
        # 5.2 -- S1, one block
        "test_b_block_life.py",
        "test_b_start.py",
        "test_b_ldt.py",
        "test_b_stress.py",
        # 5.3 -- S2, topology and propagation
        "test_b_k_of_n.py",
        "test_b_cut_linked.py",
        "test_b_cascade.py",
        "test_b_system_status.py",
        "test_b_hierarchy.py",
        # 5.4 -- S3, S4, S5, S7
        "test_b_resource_queue.py",
        "test_b_repair_resource.py",
        "test_b_spare_pool.py",
        "test_b_pm.py",
        "test_b_pm_defer.py",
        "test_b_trigger.py",
        # 5.5 -- S6, dependencies
        "test_b_dependency.py",
        "test_b_dependency_wait.py",
        # 5.6 -- S8, standby
        "test_b_cold_standby.py",
        "test_b_cold_priority.py",
        "test_b_node_standby.py",
        # 5.7 -- S9, S10
        "test_b_phases.py",
        "test_b_events.py",
        # 5.8 -- S11, S12
        "test_b_capacity.py",
        "test_b_report_aggregate.py",
        "test_b_cost.py",
        "test_b_weak_link.py",
        "test_b_spf.py",
        # 5.9 -- S13, the gates
        "test_b_ladder.py",
        "test_b_properties.py",
        "test_b_state_coverage.py",
        "test_b_behaviour_coverage.py",
        # Cluster-pinning files. The plan's 5.1-5.9 catalogue is organised by
        # layer, but most of the corpus's transitions are not reachable from a
        # single rung -- they need a model built to provoke one mechanism. These
        # are organised by mechanism cluster instead, which is how they were
        # actually written, and between them they took the pinned count from
        # 43 to 198.
        "test_b_trans_phases.py",
        "test_b_trans_block.py",
        "test_b_trans_graph.py",
        "test_b_trans_runs.py",
        "test_b_trans_final.py",
        "test_b_final_rows.py",
        "test_b_cascade_tail.py",
        # test_b_cascade.py is already listed under 5.3; test_b_wait_sites.py
        # asserts timelines too, and the [unknown file] check reads references
        # out of the manifest, so it has to be nameable.
        "test_b_wait_sites.py",
    }
)

#: The classes a manifest entry may carry, and the ones that owe a reason.
CLASSES = ("pinned", "planned", "unreachable", "artefact")
REASON_REQUIRED = ("unreachable", "artefact")

_FILENAME = re.compile(r"(test_b_[a-z0-9_]+\.py)")


class Entry(NamedTuple):
    """One `[[transition]]` row of the manifest."""

    transition: inv.Transition
    klass: str
    reason: str
    reference: str


@dataclass(frozen=True)
class Manifest:
    """The manifest as the gate reads it."""

    entries: dict[inv.Transition, Entry]
    ratchet: dict[str, int]
    problems: list[str] = field(default_factory=list)

    def classes(self) -> Counter[str]:
        return Counter(e.klass for e in self.entries.values())

    def of_class(self, klass: str) -> set[inv.Transition]:
        return {t for t, e in self.entries.items() if e.klass == klass}


@dataclass(frozen=True)
class Literals:
    """What `tests/behaviour/` asserts, as found by the `ast` pass."""

    transitions: dict[inv.Transition, list[str]]
    """`(object_type, from, to)` -> ["test_b_pm.py:41", ...] in source order."""

    calls: int
    """`assert_timeline(...)` call sites seen."""

    rows: int
    """Rows read out of those literals."""

    unreadable: list[str]
    """Rows, or calls, the parser could not turn into a transition."""


def load_manifest(path: Path = MANIFEST) -> Manifest:
    """Parse the manifest, collecting malformed rows as problems not exceptions."""
    entries: dict[inv.Transition, Entry] = {}
    ratchet: dict[str, int] = {}
    problems: list[str] = []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Manifest({}, {}, [f"{path}: cannot be read as TOML: {exc}"])
    for key, value in (raw.get("ratchet") or {}).items():
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"{path.name}: [ratchet].{key} = {value!r} is not an integer")
        else:
            ratchet[key] = value
    rows = raw.get("transition")
    if not isinstance(rows, list):
        return Manifest(entries, ratchet, [*problems, f"{path.name}: no [[transition]] rows"])
    for index, row in enumerate(rows):
        where = f"{path.name}: [[transition]] #{index + 1}"
        if not isinstance(row, dict):
            problems.append(f"{where}: not a table")
            continue
        missing = {"object", "from_state", "to_state", "class"} - set(row)
        if missing:
            problems.append(f"{where}: missing key(s) {sorted(missing)}")
            continue
        transition = inv.Transition(row["object"], row["from_state"], row["to_state"])
        klass = row["class"]
        if klass not in CLASSES:
            problems.append(f"{where}: class = {klass!r} is not one of {list(CLASSES)}")
            continue
        if transition in entries:
            problems.append(
                f"{where}: {transition} is already classified as "
                f"{entries[transition].klass!r}; classify it exactly once"
            )
            continue
        entries[transition] = Entry(
            transition,
            klass,
            str(row.get("reason", "")),
            str(row.get("reference", "")),
        )
    return Manifest(entries, ratchet, problems)


def _const(node: ast.expr) -> str | None:
    """The value of a string literal, or None for anything computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _prefixed_type(ident: str, object_types: frozenset[str]) -> str | None:
    """`"Block:A"` -> `"Block"`; a bare name or an unknown prefix -> None."""
    prefix, sep, _ = ident.partition(":")
    return prefix if sep and prefix in object_types else None


def _row_transition(row: ast.expr, object_types: frozenset[str]) -> inv.Transition | None:
    """One `assert_timeline` row to a transition, or None if not readable.

    The last two elements are always the states -- they are the claim. The
    object type comes from `"Block:A"` in the second element, from a bare
    object type in a five-element row, or defaults to `Block` because rule B5
    says the literal is a list of `(time, block, from, to)` tuples.
    """
    if not isinstance(row, (ast.Tuple, ast.List)):
        return None
    elements = row.elts
    if len(elements) < 4:
        return None
    fields = [_const(element) for element in elements]
    from_state, to_state = fields[-2], fields[-1]
    if from_state is None or to_state is None:
        return None
    ident = fields[1]
    object_type = _prefixed_type(ident, object_types) if ident is not None else None
    if object_type is None and ident in object_types and len(elements) >= 5:
        object_type = ident
    if object_type is None:
        object_type = "Block" if ident is not None else None
    if object_type is None:
        return None
    return inv.Transition(object_type, from_state, to_state)


def _is_assert_timeline(node: ast.expr) -> bool:
    """`assert_timeline(...)` or `harness.assert_timeline(...)`."""
    if isinstance(node, ast.Name):
        return node.id == "assert_timeline"
    if isinstance(node, ast.Attribute):
        return node.attr == "assert_timeline"
    return False


def _module_table(tree: ast.Module) -> dict[str, ast.expr]:
    """Module-level `NAME = <literal>` bindings.

    The suite writes its literals inline in the `assert_timeline` call, but a
    table (`TIMELINES = {1: [...], 2: [...]}`) is a legitimate way to write
    them too, and the gate should see through either. Resolving a name is
    still a parse, not a run: a timeline *computed* from the engine's own
    output resolves to nothing and is reported as unreadable, which is the
    answer it deserves.
    """
    table: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    table[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            table[node.target.id] = node.value
    return table


def _resolve_rows(node: ast.expr, table: dict[str, ast.expr], seen: frozenset[str]) -> list[ast.expr] | None:
    """The literal rows an expression denotes, or None if it is computed.

    A subscript such as `TIMELINES[rung]` contributes *every* value of the dict
    it indexes: a parametrised call asserts each of them, so each is pinned.
    """
    if isinstance(node, ast.List):
        return list(node.elts)
    if isinstance(node, ast.Tuple):
        return [node]
    if isinstance(node, ast.Dict):
        rows: list[ast.expr] = []
        for value in node.values:
            if value is None:
                return None
            resolved = _resolve_rows(value, table, seen)
            if resolved is None:
                return None
            rows += resolved
        return rows
    if isinstance(node, ast.Name):
        if node.id in seen:
            return None
        target = table.get(node.id)
        if target is None:
            return None
        return _resolve_rows(target, table, seen | {node.id})
    if isinstance(node, ast.Subscript):
        return _resolve_rows(node.value, table, seen)
    return None


def scan_literals(directory: Path = BEHAVIOUR, object_types: frozenset[str] = frozenset()) -> Literals:
    """Every transition asserted by an `assert_timeline` literal in `directory`.

    The modules are parsed, never imported and never run: importing them
    would execute the suite, and running it would make the gate's input a
    side effect of the suite passing.
    """
    transitions: dict[inv.Transition, list[str]] = {}
    unreadable: list[str] = []
    calls = rows = 0
    for path in sorted(directory.glob("*.py")) if directory.is_dir() else []:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            unreadable.append(f"{path.name}: cannot be parsed: {exc}")
            continue
        table = _module_table(tree)
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not _is_assert_timeline(call.func):
                continue
            calls += 1
            literals = [
                resolved
                for argument in (*call.args, *(k.value for k in call.keywords))
                if (resolved := _resolve_rows(argument, table, frozenset())) is not None
            ]
            if not literals:
                unreadable.append(
                    f"{path.name}:{call.lineno}: assert_timeline() with no literal list -- "
                    "the gate cannot see what it asserts"
                )
                continue
            for literal in literals:
                for row in literal:
                    rows += 1
                    transition = _row_transition(row, object_types)
                    if transition is None:
                        unreadable.append(
                            f"{path.name}:{getattr(row, 'lineno', call.lineno)}: "
                            f"unreadable timeline row {ast.dump(row)[:80]}"
                        )
                        continue
                    transitions.setdefault(transition, []).append(
                        f"{path.name}:{getattr(row, 'lineno', call.lineno)}"
                    )
    return Literals(transitions, calls, rows, unreadable)


def compare(
    inventory: inv.Inventory,
    literals: Literals,
    manifest: Manifest | None = None,
) -> tuple[dict, list[str]]:
    """Corpus vs manifest vs literals, as a state dict and a problem list."""
    manifest = manifest if manifest is not None else load_manifest()
    problems: list[str] = list(manifest.problems)
    entries = manifest.entries
    corpus_set = set(inventory.transitions)

    def where(transition: inv.Transition) -> str:
        captures = inventory.transitions.get(transition) or []
        return "\n      from capture: " + "\n                   ".join(captures)

    for transition in sorted(corpus_set - set(entries)):
        problems.append(f"  [unclassified] {transition}{where(transition)}")
    for transition in sorted(set(entries) - corpus_set):
        problems.append(
            f"  [stale] {transition} is classified {entries[transition].klass!r} but no "
            f"capture in the corpus performs it any more -- either the corpus changed or "
            f"the entry is stale; drop it in the same commit that changed the corpus, "
            f"and say in that commit which capture changed"
        )
    for transition in sorted(set(entries) & corpus_set):
        entry = entries[transition]
        if entry.klass in REASON_REQUIRED and not entry.reason.strip():
            problems.append(
                f"  [no reason] {transition} is {entry.klass!r} with an empty reason"
                f"{where(transition)}"
            )
        if entry.klass in ("pinned", "planned"):
            found = _FILENAME.search(entry.reference)
            if not found:
                problems.append(
                    f"  [no reference] {transition} is {entry.klass!r} but names no "
                    f"test_b_*.py file to pin it"
                )
            elif found.group(1) not in CATALOGUE_FILES:
                problems.append(
                    f"  [unknown file] {transition} is {entry.klass!r} but references "
                    f"{found.group(1)}, which is not in the rung catalogue"
                )
        if entry.klass == "pinned" and transition not in literals.transitions:
            problems.append(
                f"  [unbacked claim] {transition} is classified 'pinned', but no "
                f"assert_timeline literal under tests/behaviour/ asserts it"
                f"{where(transition)}"
            )
    return {"inventory": inventory, "manifest": manifest, "literals": literals}, problems


def ratchet_problems(manifest: Manifest, literals: Literals) -> list[str]:
    """The two baselines. Both may only go up; a fall is a red build."""
    problems: list[str] = []
    classified = len(manifest.entries)
    baseline = manifest.ratchet.get("classified")
    if baseline is None:
        problems.append("  [no baseline] the manifest has no [ratchet].classified")
    elif classified < baseline:
        problems.append(
            f"  [regression] {classified} transitions classified, was {baseline}. "
            f"A classification may only be added, never deleted to make a run green."
        )
    pinned = len(manifest.of_class("pinned"))
    baseline = manifest.ratchet.get("pinned")
    if baseline is None:
        problems.append("  [no baseline] the manifest has no [ratchet].pinned")
    elif pinned < baseline:
        problems.append(
            f"  [regression] {pinned} corpus transitions are pinned by a committed "
            f"assert_timeline literal, was {baseline}. Behaviour coverage went "
            f"backwards: either a 'pinned' entry was reclassified or the test that "
            f"asserted it was deleted. `git diff HEAD~ -- tests/behaviour/` names "
            f"which -- a ratchet that cannot be lowered is the whole mechanism."
        )
    return problems


def summary(state: dict) -> str:
    """The `--cov-report`-style progress line: a denominator and a numerator."""
    inventory: inv.Inventory = state["inventory"]
    manifest: Manifest = state["manifest"]
    literals: Literals = state["literals"]
    classes = manifest.classes()
    live = len(manifest.of_class("pinned")) + len(manifest.of_class("planned"))
    backed = len(manifest.of_class("pinned") & set(literals.transitions))
    corroborated = len(set(inventory.transitions) & set(literals.transitions))
    pinned = manifest.ratchet.get("pinned", 0)
    rows = [
        "behaviour coverage",
        f"  corpus: {len(inventory.captures)} captures, "
        f"{len(inventory.transitions)} distinct transitions",
        f"  manifest: {len(manifest.entries)} classified = "
        + " + ".join(f"{classes.get(k, 0)} {k}" for k in CLASSES),
        f"  pinned by tests/behaviour/: {backed} "
        f"(ratchet baseline {pinned}) of {live} live transitions"
        + ("" if backed >= pinned else "  <-- BELOW BASELINE"),
        f"  asserted by a literal at all: {corroborated} "
        f"(from {literals.calls} assert_timeline calls, {literals.rows} rows)",
    ]
    if literals.unreadable:
        rows.append(f"  UNREADABLE by the gate: {len(literals.unreadable)}")
    return "\n".join(rows)


def _fail(title: str, problems: list[str]) -> None:
    shown = problems[:25]
    tail = (
        f"\n  ... and {len(problems) - len(shown)} more" if len(problems) > len(shown) else ""
    )
    pytest.fail(
        f"{title}: {len(problems)} problem(s)\n" + "\n".join(shown) + tail,
        pytrace=False,
    )


@pytest.fixture(scope="module")
def state() -> dict:
    """The whole comparison, computed once for the module (about 11 s).

    The corpus scan dominates, so it happens exactly once: five tests share
    one 520 MB read rather than paying for it five times.
    """
    inventory = inv.collect()
    literals = scan_literals(object_types=frozenset(inventory.by_type))
    state, problems = compare(inventory, literals)
    return {**state, "problems": problems}


def test_denominator_is_the_318_transitions_of_section_1_6(state: dict) -> None:
    """The documented count, re-derived from the captures themselves."""
    inventory: inv.Inventory = state["inventory"]
    assert len(inventory.transitions) == inv.DOCUMENTED_TOTAL, (
        f"the corpus now exhibits {len(inventory.transitions)} transitions, "
        f"not the documented {inv.DOCUMENTED_TOTAL} "
        f"measured: {inv.render_report(inventory)}"
    )
    assert inventory.by_type == inv.DOCUMENTED, (
        f"per-object-type drift: {inventory.by_type} != {inv.DOCUMENTED}"
    )


def test_every_corpus_transition_is_classified(state: dict) -> None:
    """Both directions: nothing unclassified, nothing classified in the dark."""
    if state["problems"]:
        _fail("behaviour coverage: the manifest and the corpus disagree",
              state["problems"])


def test_every_assert_timeline_row_is_visible_to_the_gate(state: dict) -> None:
    """A row the parser cannot read would silently weaken the gate."""
    unreadable: list[str] = state["literals"].unreadable
    if unreadable:
        _fail("the gate cannot read every asserted row", unreadable)


def test_pinned_coverage_does_not_regress(state: dict) -> None:
    """The ratchet. This is the test that makes the suite's progress monotone."""
    failures = ratchet_problems(state["manifest"], state["literals"])
    if failures:
        _fail("behaviour coverage regressed", failures)


def test_report(state: dict, capsys: pytest.CaptureFixture[str]) -> None:
    """Print the progress count.

    Visible with `pytest -s`, or without pytest at all via
    `python tests/behaviour/test_b_behaviour_coverage.py --cov-report`. This
    is how progress is made visible: the number is in CI output, and it only
    ever goes up.
    """
    text = summary(state)
    if state["problems"]:
        text += f"\n{len(state['problems'])} problem(s) outstanding"
    with capsys.disabled():
        print("\n" + text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cov-report", action="store_true", help="print the summary and stop")
    ap.add_argument("-j", "--workers", type=int, default=0, help="0 = min(8, cpus)")
    a = ap.parse_args()
    inventory = inv.collect(a.workers)
    literals = scan_literals(object_types=frozenset(inventory.by_type))
    state, problems = compare(inventory, literals)
    print(summary(state))
    for problem in ratchet_problems(state["manifest"], literals) + problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
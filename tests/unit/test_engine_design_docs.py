"""The engine design pages (`docs/engine-design/*.adoc`) must not drift from
the code they describe.

A page names code in backticks. This test checks, for every page:

- each `BlockProcess.<member>` is still a member of `BlockProcess`;
- each attribute in a "State it owns" table is still assigned on `self`;
- each `<module>.<name>` under `raptor/core/engine` still exists.

A rename then fails CI in the PR that caused it,
instead of leaving a stale page behind.
"""
from __future__ import annotations

import dataclasses
import importlib
import inspect
import re
from pathlib import Path

import pytest

from raptor.core.engine import block_lifecycle
from raptor.core.engine.block_lifecycle import BlockProcess

_REPO = Path(__file__).resolve().parents[2]
_PAGES = sorted((_REPO / "docs" / "engine-design").glob("*.adoc"))
_CLASS_SRC = inspect.getsource(BlockProcess)
_ENGINE_MODULES = {p.stem for p in (_REPO / "raptor" / "core" / "engine").glob("*.py")}


def _is_member(name: str) -> bool:
    return hasattr(BlockProcess, name) or re.search(rf"\bself\.{re.escape(name)}\b", _CLASS_SRC) is not None


@pytest.mark.parametrize("page", _PAGES, ids=lambda p: p.name)
def test_blockprocess_members_exist(page: Path) -> None:
    text = page.read_text()
    missing = sorted({m for m in re.findall(r"`BlockProcess\.(\w+)`", text) if not _is_member(m)})
    assert not missing, f"{page.name} names BlockProcess members that no longer exist: {missing}"


def _record_fields() -> dict[str, set[str]]:
    """`self.<attr> = <Record>()` in `BlockProcess.__init__` -> that record's
    dataclass field names."""
    init = inspect.getsource(BlockProcess.__init__)
    out = {}
    for attr, cls_name in re.findall(r"self\.(\w+) = (\w+)\(\)", init):
        cls = getattr(block_lifecycle, cls_name, None)
        if cls is not None and dataclasses.is_dataclass(cls):
            out[attr] = {f.name for f in dataclasses.fields(cls)}
    return out


@pytest.mark.parametrize("page", _PAGES, ids=lambda p: p.name)
def test_state_table_attributes_exist(page: Path) -> None:
    text = page.read_text()
    section = re.search(r"^== State it owns\n(.*?)(?=^== )", text, re.S | re.M)
    if section is None:
        pytest.skip("no state table on this page")
    rows = [line for line in section.group(1).splitlines()
            if line.startswith("| ") and not line.startswith("|===")][1:]  # [1:]: header row
    records = _record_fields()
    missing = []
    for row in rows:
        # Record classes are CamelCase; attributes are snake_case, possibly
        # `record.field`. Source names (`FailTimeLeft`, `startVal`) contain
        # capitals, so only a name `block_lifecycle` defines is treated as a
        # record class, and mixed-case names are skipped.
        for name in re.findall(r"`([A-Za-z_][\w.]*)`", row):
            if name[0].isupper():
                if hasattr(block_lifecycle, name) and not dataclasses.is_dataclass(getattr(block_lifecycle, name)):
                    missing.append(name)
                continue
            if name != name.lower() or name.endswith(".py"):
                continue  # a source name (`startVal`) or a file name
            head, _, field = name.partition(".")
            if field:
                if field not in records.get(head, set()):
                    missing.append(name)
            elif not _is_member(head):
                missing.append(name)
    assert not missing, f"{page.name}'s state table lists state BlockProcess no longer has: {sorted(set(missing))}"


@pytest.mark.parametrize("page", _PAGES, ids=lambda p: p.name)
def test_engine_module_references_exist(page: Path) -> None:
    text = page.read_text()
    missing = []
    for module, name in set(re.findall(r"`(\w+)\.(\w+)`", text)):
        if module not in _ENGINE_MODULES or name == "py":  # `blocks.py` is a file name
            continue
        mod = importlib.import_module(f"raptor.core.engine.{module}")
        if not hasattr(mod, name):
            missing.append(f"{module}.{name}")
    assert not missing, f"{page.name} names engine code that no longer exists: {sorted(missing)}"


def test_module_function_references_in_block_lifecycle_page_resolve() -> None:
    """Module-level functions the page names without a module prefix."""
    assert hasattr(block_lifecycle, "get_start_cond")


def test_every_wait_site_is_observed_once() -> None:
    """Each `WaitSite` names exactly one `resume = yield ...` in
    `BlockProcess`, so the state-coverage matrix cannot silently lose or
    double-count a wait."""
    from raptor.core.engine.wait_sites import WaitSite

    src = inspect.getsource(block_lifecycle)
    counts = {site.name: src.count(f"self._observe_wake(WaitSite.{site.name}, resume)") for site in WaitSite}
    assert all(n == 1 for n in counts.values()), counts
    assert src.count("self._observe_wake(") == len(WaitSite), "an _observe_wake call names no WaitSite"


def test_state_coverage_page_lists_every_wait_site() -> None:
    from raptor.core.engine.wait_sites import WaitSite

    page = (_REPO / "docs" / "engine-design" / "state-coverage.adoc").read_text()
    missing = [str(site) for site in WaitSite if f"| `{site}` |" not in page]
    assert not missing, f"state-coverage.adoc has no row for {missing}: regenerate it with tools/state_coverage.py --write"


def test_every_wait_site_has_a_wake_policy() -> None:
    from raptor.core.engine.wait_sites import WAKE_POLICY, WaitSite

    assert set(WAKE_POLICY) == set(WaitSite)


def test_generated_design_pages_are_current() -> None:
    # `tools/gen_engine_docs.py --write` regenerates them.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen_engine_docs", _REPO / "tools" / "gen_engine_docs.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    stale = [name for name, build in gen.GENERATED.items()
             if (_REPO / "docs" / "engine-design" / name).read_text() != build()]
    assert not stale, f"regenerate with gen_engine_docs.py --write: {stale}"

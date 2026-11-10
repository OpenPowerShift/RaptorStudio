"""Import layering: the engine, API and MCP never depend on the GUI or on Qt.

Dependencies point one way: ``gui -> api -> core`` (``mcp`` and ``cli`` sit beside the
GUI). Headless logic the GUI and the API both need lives in ``raptor.core``, not in
``raptor.gui``. Imports inside functions count: a lazy import is still a dependency.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "raptor"

# package -> top-level names it must not import
FORBIDDEN = {
    "core": ("raptor.gui", "raptor.mcp", "raptor.api", "raptor.cli", "PySide6"),
    "api": ("raptor.gui", "raptor.mcp", "raptor.cli", "PySide6"),
    "mcp": ("raptor.gui", "PySide6"),
}

# (file, imported module): every entry is debt, not design. Shrink this, never grow it.
ALLOWED: set[tuple[str, str]] = {
    # Loading an .rbds recomputes linked rate fields through the API's rate code.
    # `propagate_rates` should move down into core once api.build.store_numeric does.
    ("raptor/core/fileio/rbds.py", "raptor.api.rates"),
    ("raptor/core/fileio/rbds.py", "raptor.api.rates.propagate_rates"),
}


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module
            for alias in node.names:
                yield f"{node.module}.{alias.name}"


def _violations(package: str):
    for path in sorted((ROOT / package).rglob("*.py")):
        rel = path.relative_to(ROOT.parent).as_posix()
        for mod in set(_imports(path)):
            for banned in FORBIDDEN[package]:
                if (mod == banned or mod.startswith(banned + ".")) \
                        and (rel, mod) not in ALLOWED:
                    yield f"{rel} imports {mod}"


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_lower_layers_do_not_import_upward(package):
    bad = sorted(set(_violations(package)))
    assert not bad, "layering violations:\n  " + "\n  ".join(bad)


# The GUI reaches the simulator through `raptor.api`; only these files still import
# the engine directly (status colours, FMECA, distribution stats, the run loop).
# A ratchet: remove a name when its last direct import goes, never add one.
GUI_ENGINE_IMPORTERS = frozenset({
    "raptor/gui/ui/analytic_dialog.py", "raptor/gui/ui/block_labels.py",
    "raptor/gui/ui/data_tables.py",
    "raptor/gui/ui/dialogs.py", "raptor/gui/ui/fev_inspector.py",
    "raptor/gui/ui/fev_mixin.py", "raptor/gui/ui/inspector.py",
    "raptor/gui/ui/main_window.py", "raptor/gui/ui/results.py",
    "raptor/gui/ui/run_experiment_mixin.py", "raptor/gui/ui/run_panel.py",
    # moved out of main_window.py, not new:
    "raptor/gui/ui/background_run_mixin.py", "raptor/gui/ui/file_io_mixin.py",
    "raptor/gui/ui/overlay_mixin.py", "raptor/gui/ui/status_summary_mixin.py",
    "raptor/gui/ui/step_mixin.py", "raptor/gui/ui/diagram_element_edit_mixin.py",
    "raptor/gui/ui/results_graphs_mixin.py", "raptor/gui/ui/results_pages_mixin.py",
    "raptor/gui/ui/results_raw_mixin.py", "raptor/gui/ui/distro_editor.py",
    "raptor/gui/ui/inspector_rows_mixin.py",
})
_ENGINE = ("raptor.core.engine", "raptor.core.rng", "raptor.core.scheduler",
           "raptor.core.analytical", "raptor.core.run_experiment")


def test_gui_engine_imports_only_shrink():
    found = set()
    for path in (ROOT / "gui").rglob("*.py"):
        if any(m.startswith(_ENGINE) for m in _imports(path)):
            found.add(path.relative_to(ROOT.parent).as_posix())
    new = sorted(found - GUI_ENGINE_IMPORTERS)
    assert not new, f"new direct engine imports in the GUI (use raptor.api): {new}"
    gone = sorted(GUI_ENGINE_IMPORTERS - found)
    assert not gone, f"no longer import the engine; drop from the ratchet: {gone}"

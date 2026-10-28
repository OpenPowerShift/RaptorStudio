"""Bundled example models.

Discovers the tutorial models (`new_docs/models/*.rbds`, shipped with every
install) and, in a checkout, the golden fixtures (`tests/golden/<Name>/
input.rbd[s]`), and resolves an example *name* to a file path.  Kept separate
from the tool modules so both ``list_models`` and ``open_model`` (and the
``raptor://examples`` resource) share one definition.
"""
from __future__ import annotations

from pathlib import Path

from raptor.bundled import data_root

REPO = data_root()
GOLDEN = REPO / "tests" / "golden"
NEW_DOCS_MODELS = REPO / "new_docs" / "models"

#: Well-known fixtures first (stable, always present), then whatever else
#: the checkout contains.
_PREFERRED = ["MotorDemo", "Cascade", "Phasing3", "SpaceStation"]


def _model_file(directory: Path) -> Path | None:
    for name in ("input.rbds", "input.rbd"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _examples() -> dict[str, Path]:
    """Every example name -> model file; the first directory wins a clash."""
    found: dict[str, Path] = {}
    if GOLDEN.is_dir():
        for child in sorted(GOLDEN.iterdir()):
            if child.is_dir():
                model = _model_file(child)
                if model is not None:
                    found.setdefault(child.name, model)
    if NEW_DOCS_MODELS.is_dir():
        for model in sorted(NEW_DOCS_MODELS.iterdir()):
            if model.is_file() and model.suffix in (".rbds", ".rbd"):
                found.setdefault(model.stem, model)
    return found


def list_examples() -> list[dict[str, str]]:
    """Return ``[{name, path}]`` for every discoverable example model."""
    found = _examples()
    ordered = [n for n in _PREFERRED if n in found]
    ordered += sorted(n for n in found if n not in _PREFERRED)
    return [{"name": n, "path": str(found[n])} for n in ordered]


def find_example(name: str) -> Path | None:
    """Resolve an example name (case-insensitive) to a model file path."""
    wanted = name.strip().lower()
    for found_name, model in _examples().items():
        if found_name.lower() == wanted:
            return model
    return None

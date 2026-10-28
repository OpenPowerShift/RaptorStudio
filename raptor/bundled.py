"""Where the bundled docs and tutorial models live.

A checkout keeps `docs/` and `new_docs/` beside the `raptor` package. A wheel
installs them as data files under the install scheme's data directory:
`sys.prefix` for a venv or pipx, the user base for `pip install --user`.
PyInstaller puts them in the bundle root, which is `sys.prefix` when frozen.
"""
from __future__ import annotations

import site
import sys
from functools import cache
from pathlib import Path

_CHECKOUT = Path(__file__).resolve().parent.parent


def candidate_roots() -> list[Path]:
    """Every directory that may hold `docs/` and `new_docs/`, nearest first."""
    roots = [_CHECKOUT, Path(sys.prefix)]
    user_base = getattr(site, "USER_BASE", None)
    if user_base:
        roots.append(Path(user_base))
    out: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in out:
            out.append(resolved)
    return out


@cache
def data_root() -> Path:
    """The first candidate root that holds the tutorial book chapters (falls
    back to the checkout root, so a missing install reports clean
    not-found errors rather than crashing)."""
    for root in candidate_roots():
        if (root / "new_docs" / "chapters").is_dir():
            return root
    return _CHECKOUT

"""Set uv.lock's own raptor-studio version to pyproject.toml's.

release-please bumps pyproject.toml but cannot edit uv.lock, so run this on
the release PR's branch (RELEASING.md, "Merge the release PR"):

    python tools/sync_uv_lock_version.py          # rewrite uv.lock
    python tools/sync_uv_lock_version.py --check  # exit 1 if they differ

Only the one `version = "..."` line under `name = "raptor-studio"` changes.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_ENTRY = re.compile(r'(\[\[package\]\]\nname = "raptor-studio"\nversion = ")([^"]*)(")')


def sync(repo: Path = REPO, *, write: bool = True) -> tuple[str, str]:
    """Return (version in uv.lock, version in pyproject.toml)."""
    wanted = tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]
    lock = repo / "uv.lock"
    text = lock.read_text()
    found = _ENTRY.findall(text)
    if len(found) != 1:
        raise SystemExit(f"expected one raptor-studio entry in uv.lock, found {len(found)}")
    have = found[0][1]
    if write and have != wanted:
        lock.write_text(_ENTRY.sub(lambda m: m.group(1) + wanted + m.group(3), text))
    return have, wanted


def main(argv: list[str]) -> int:
    check = "--check" in argv
    have, wanted = sync(write=not check)
    if have == wanted:
        print(f"uv.lock already records raptor-studio {wanted}")
        return 0
    if check:
        print(f"uv.lock records {have}; pyproject.toml says {wanted}")
        return 1
    print(f"uv.lock: raptor-studio {have} -> {wanted}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

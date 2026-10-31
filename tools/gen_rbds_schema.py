#!/usr/bin/env python3
"""Write docs/rbds.schema.json from the format's field maps.

    python tools/gen_rbds_schema.py [--write]

Without --write, exits 1 when the committed file is stale (the test does the same)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from raptor.core.fileio.rbds_schema import build_schema  # noqa: E402

TARGET = ROOT / "docs" / "rbds.schema.json"


def render() -> str:
    return json.dumps(build_schema(), indent=1, sort_keys=False) + "\n"


def main() -> int:
    text = render()
    if "--write" in sys.argv:
        TARGET.write_text(text, encoding="utf-8")
        print(f"wrote {TARGET}")
        return 0
    if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != text:
        print(f"{TARGET} is stale; run python tools/gen_rbds_schema.py --write")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

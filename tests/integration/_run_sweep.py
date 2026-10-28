"""Guarded launcher for the UI sweeps in tests/integration/sweeps/.

Executes a sweep with ``__name__ == "__main__"`` while leaving the
process's actual ``sys.modules['__main__']`` as this guarded module.
That matters for ``ui_sweep4.py``'s parallel-worker check: the
engine's parallel batch spawns worker processes, and Python's spawn
re-imports ``__main__`` in the child. If the sweep were ``__main__``
(and, as a plain script, unguarded), the child would re-run the whole
sweep; here the child re-imports this file, whose top-level work is under
``if __name__ == "__main__"``.

Uses exec-with-``__name__`` rather than ``runpy`` because ``runpy``
temporarily makes the target ``sys.modules['__main__']`` too.

The sweep's own directory goes on ``sys.path`` before the exec, so a
sweep can ``import _sweep_base`` (the shared modal-stub / Recorder /
report harness) without any packaging of its own. A sweep's own
``sys.path.insert(0, REPO)`` still wins for ``raptor`` -- this is only
there to make the sibling module importable.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _run_sweep.py <sweep.py>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    sweep_dir = str(Path(path).resolve().parent)
    if sweep_dir not in sys.path:
        sys.path.insert(0, sweep_dir)
    with open(path, encoding="utf-8") as fh:
        code = compile(fh.read(), path, "exec")
    globals_dict = {
        "__name__": "__main__",
        "__file__": path,
        "__builtins__": __builtins__,
    }
    exec(code, globals_dict)   # noqa: S102 -- trusted local sweep script
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

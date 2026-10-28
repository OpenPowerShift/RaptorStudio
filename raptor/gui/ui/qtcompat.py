"""Small typing helpers for PySide6 calls whose stubs return `Optional`."""
from __future__ import annotations


def req[T](x: T | None) -> T:
    """Return `x`, which the Qt API guarantees is set at this call site (the stubs say `| None`)."""
    assert x is not None
    return x

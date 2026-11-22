"""Public API for share-safe copies (see `raptor.core.redact`)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from raptor.core.redact import (
    ANNOTATION_POLICIES, RedactOptions, RedactReport, declassify,
    write_share_copy,
)

__all__ = ["ANNOTATION_POLICIES", "RedactOptions", "RedactReport",
           "declassify", "share_safe_copy"]


def share_safe_copy(rbd: Any, path: str | Path,
                    options: RedactOptions | None = None, *,
                    source: str | Path | None = None
                    ) -> tuple[Path, RedactReport]:
    return write_share_copy(rbd, path, options, source=source)

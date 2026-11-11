"""The GUI's name for the disk-backed result streams, which live in
`raptor.core.result_streams` so the API and the engine's worker processes
can spool to disk too."""
from __future__ import annotations

from raptor.core.result_streams import ResultStream

__all__ = ["ResultStream"]

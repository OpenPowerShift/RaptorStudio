"""How big the analytical solver's BDD may grow on this machine.

The BDD lives in memory inside the application (the GUI solves on a thread of
its own process), and nothing is freed while it is built. A diagram that
outgrows the machine's memory gets the whole application killed by the
operating system, unsaved edits and all. So there is a limit, but it is the
user's to raise, up to a ceiling worked out from the memory that is free.

Measured cost (2026-11-03, the ProjectX BESS hierarchy refused at 2M nodes):
about 343 bytes per node created, counting the unique table and the
operation caches, and about 4.4 s to build 2M nodes. Evaluation adds a
batch table of at most 128 MB (`bdd._BATCH_BYTES`) on top. Importance makes
two walks per element and MTTF hundreds; they are vectorised and batched,
but a large diagram is still slower to use as well as to build.
"""
from __future__ import annotations

import os
import sys

#: Memory per BDD node created, rounded up from the measured ~343 bytes.
BYTES_PER_NODE = 350
#: The default limit: a few seconds and well under 1 GB.
DEFAULT_BDD_NODES = 2_000_000
#: Share of the free memory a solve may plan to use. The rest covers the
#: model, the application and the transient doubling when a table resizes.
_MEMORY_SHARE = 0.5
#: Used when the free memory cannot be read.
_FALLBACK_CEILING = 8_000_000
#: Never offer more than this, whatever the machine.
_ABSOLUTE_CEILING = 500_000_000


def available_memory() -> int | None:
    """Bytes of memory free for new use, or None when it cannot be read."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
            return None
        if sys.platform == "win32":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
                    ctypes.byref(status)):
                return int(status.ullAvailPhys)
            return None
        # macOS and other POSIX: total physical memory is the best cheap
        # figure; take half of it as "free".
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * size) // 2
    except (OSError, ValueError, AttributeError):
        return None


def max_bdd_nodes_allowed() -> int:
    """The largest limit this machine can take, read fresh each call."""
    free = available_memory()
    if free is None:
        return _FALLBACK_CEILING
    ceiling = int(free * _MEMORY_SHARE / BYTES_PER_NODE)
    return max(DEFAULT_BDD_NODES, min(ceiling, _ABSOLUTE_CEILING))


def memory_text(nodes: int) -> str:
    """Worst-case memory for ``nodes``, in words: "about 0.7 GB"."""
    size = nodes * BYTES_PER_NODE
    if size >= 1024 ** 3:
        return f"about {size / 1024 ** 3:.1f} GB"
    return f"about {max(1, round(size / 1024 ** 2)):,} MB"

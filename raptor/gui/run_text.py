"""Status-bar wording for a finished or stopped run (pure: no Qt, no state)."""
from __future__ import annotations


def run_outcome_text(*, stopped: bool, completed: int, logs: int,
                     event_log_lines: int) -> tuple[str, int]:
    """The status line for how a batch ended, and how long to show it (ms; 0 = until
    replaced).

    ``completed`` is how many runs produced results, ``logs`` how many event logs
    exist (one more than ``completed`` when a stopped run left a partial log).
    """
    if not stopped:
        # State success explicitly so a finished run is never mistaken for an aborted one.
        return (f"Completed {logs} run(s) successfully, "
                f"{event_log_lines} event-log lines"), 0
    if completed == 0:
        return "Stopped before any run completed — no results", 10000
    if completed < logs:
        return ("Stopped — partial event log kept for the interrupted "
                f"run, {event_log_lines} event-log lines"), 10000
    return (f"Stopped after {completed} run(s), "
            f"{event_log_lines} event-log lines"), 0


def _filename_dir(exc: BaseException) -> str:
    """The folder the failing write was in, when the error names a file."""
    import os
    name = getattr(exc, "filename", None)
    return os.path.dirname(str(name)) if name else ""


def disk_full_text(exc: BaseException) -> str | None:
    """Plain words for a run that ran out of disk space, or None.

    The result streams (event log, end-of-run report, plots, interim cost)
    are spooled to the temporary folder while the batch runs; a full disk
    or a per-user quota on that folder used to surface as a raw
    ``OSError: [Errno 122]`` traceback.
    """
    import errno
    import os
    import shutil
    import tempfile
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in (
                errno.ENOSPC, getattr(errno, "EDQUOT", 122)):
            break
        current = current.__cause__ or current.__context__
    else:
        return None
    if current is None:
        return None
    folder = _filename_dir(current) or tempfile.gettempdir()
    quota = current.errno == getattr(errno, "EDQUOT", 122)
    try:
        free = shutil.disk_usage(folder).free
        free_text = f" ({free / 2**30:.1f} GB free there now)"
    except OSError:
        free_text = ""
    reason = ("your disk quota is used up" if quota
              else "the disk is full")
    return (
        f"The run stopped because {reason} in {folder}{free_text}. "
        "While a batch runs, its results (event log, reports, plots and "
        "interim costs) are written there.\n\n"
        "To run again:\n"
        "• free some space on that disk, or\n"
        "• run fewer runs, or turn off the outputs you do not need "
        "(Detailed event log, Interim Cost, availability/capacity plots) "
        "in the Run panel, or\n"
        "• start RAPTOR Studio with TMPDIR pointing at a folder on a disk "
        f"with more room (it is now {os.environ.get('TMPDIR') or 'not set'})."
        "\n\nNo model data was lost; the model is unchanged.")

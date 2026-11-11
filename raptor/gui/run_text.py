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
    if completed < logs:
        return ("Stopped — partial event log kept for the interrupted "
                f"run, {event_log_lines} event-log lines"), 10000
    return (f"Stopped after {completed} run(s), "
            f"{event_log_lines} event-log lines"), 0

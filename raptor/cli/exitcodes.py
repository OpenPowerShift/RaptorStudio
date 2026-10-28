"""Process exit codes (documented in `raptor help exit-codes` and the man page)."""
from __future__ import annotations

OK = 0
RUNTIME = 1        # an unexpected error while running
USAGE = 2          # bad command line (argparse also uses 2)
INVALID = 3        # the model has validation errors
REFUSED = 4        # a model shape Raptor 7 cannot run (UnrunnableModel)
TIMEOUT = 5        # timed out or aborted; partial results written
OUTPUT = 6         # an output file could not be written
INTERRUPTED = 130  # Ctrl-C; partial results written

DESCRIPTIONS = {
    OK: "success",
    RUNTIME: "runtime error",
    USAGE: "usage error",
    INVALID: "model invalid (validation errors)",
    REFUSED: "model refused: a shape Raptor 7 cannot run",
    TIMEOUT: "timed out or aborted; partial results written",
    OUTPUT: "output could not be written",
    INTERRUPTED: "interrupted (Ctrl-C); partial results written",
}


class CliError(Exception):
    """An error that maps to a specific exit code and a one-line message."""

    def __init__(self, message: str, code: int = RUNTIME):
        super().__init__(message)
        self.code = code

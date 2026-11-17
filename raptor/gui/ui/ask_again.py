"""'Don't ask again' for the confirmations that come up in ordinary work.

User testing: the Results-on-screen prompt met every edit after a run, the
save prompt every run of a saved model, the long-run warning every long
run. Each now has a "Don't ask again" box (Qt's own `QMessageBox`
check box, the usual desktop pattern), and Preferences ▸ Working has one
button that brings them all back.

The save prompt is not here: its choice is already a preference
(`files/save_before_run`), and its box remembers the answer there.
"""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QMessageBox

from raptor.gui.ui import settings

#: Each confirmation that can be switched off: settings key -> what it
#: does once it is off (shown in Preferences).
CONFIRMATIONS: dict[str, str] = {
    "confirm/results_on_screen":
        "Editing after a run discards the results without asking",
    "confirm/long_run": "A long run starts without the time warning",
}

DONT_ASK = "Don't ask again"


def should_ask(key: str) -> bool:
    """Whether to show the confirmation `key`."""
    return settings.get_bool(key, True)


def add_box(box: QMessageBox, text: str = DONT_ASK) -> QCheckBox:
    """Put the "Don't ask again" box on `box`; read it after `exec()`."""
    check = QCheckBox(text)
    check.setToolTip("Preferences ▸ Working ▸ Show all confirmations again "
                     "brings it back")
    box.setCheckBox(check)
    return check


def stop_asking(key: str) -> None:
    settings.set_value(key, False)


def switched_off() -> list[str]:
    """The descriptions of the confirmations switched off now."""
    return [what for key, what in CONFIRMATIONS.items() if not should_ask(key)]


def ask_all_again() -> None:
    for key in CONFIRMATIONS:
        settings.set_value(key, True)

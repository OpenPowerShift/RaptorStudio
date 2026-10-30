"""`raptor doctor` reports the display correctly on every platform (#8)."""
from __future__ import annotations

import pytest

from raptor.cli.cmd_misc import display_status


@pytest.mark.parametrize("system, label", [("Windows", "Windows"),
                                           ("Darwin", "macOS")])
def test_windows_and_macos_have_a_native_display(system, label):
    assert display_status(system, {}) == (True, f"native ({label})")


def test_linux_with_wayland_or_x11_is_ok():
    assert display_status("Linux", {"WAYLAND_DISPLAY": "wayland-0",
                                    "DISPLAY": ":0"}) == (True, "wayland-0")
    assert display_status("Linux", {"DISPLAY": ":0"}) == (True, ":0")


def test_linux_with_neither_is_not_ok():
    assert display_status("Linux", {}) == (False, "none")


def test_the_doctor_line_matches_this_machine(capsys):
    from raptor.cli.main import main
    main(["doctor"])
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "display:" in ln)
    assert "BAD" not in line or "none" in line

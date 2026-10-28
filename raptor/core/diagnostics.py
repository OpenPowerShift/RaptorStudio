"""Raptor 7's `/d` diagnostics trace. The lines are the source's own; a real capture (2026-10-21, `tests/golden/TwoBlockRedundancy/legacy/
diagnostics_21Oct0510.TXT`) confirmed the order and text.

The file is named `<day><Mon><hour><minute>.TXT` from the launch time (`DateTime` text such as
`Sat Oct 10 12:05:11 2026`) and holds the start-up block, one `Opened file` entry per file open
and, per batch, a `Start/End Pre-Sim Analysis` pair around the model check followed by a
`StartSimulation` / `EndSimulation` pair. (The print/export trace lines are
commented out in the source and are not reproduced.)
"""
from __future__ import annotations

import time

from raptor.core.engine.domain_tree import DEV_VERSION


def diagnostics_file_name(today: str) -> str:
    day = int(today[8:10].strip() or 0)
    return f"{day}{today[4:7]}{today[11:13]}{today[14:16]}.TXT"


def startup_lines(today: str, *, install_path: str = "", file_to_open: str = "",
                  version: str = DEV_VERSION, screen: tuple[int, int] = (1920, 1080)) -> list[str]:
    return [f"System today String =  {today}",
            f"Raptor day month year = {int(today[8:10].strip() or 0)} {today[4:7]} {today[-4:]}",
            f"devVersion {version} Student", f"installPath = {install_path}", f"fileToOpen = {file_to_open}",
            f"Screen Width is: {screen[0]}", f"Screen Height is: {screen[1]}",
            "Resolution assumed to be 1280 X 1024",
            "Raptor main window initialized......",
            "Displayed bitmap = IntroStudent.bmp",
            "Prefs70.cfg opened and read", "SG2 file opened and read", "Paths.cfg opened and read"]


def pre_sim_lines(start: str | None = None, end: str | None = None, *, errors: bool = False) -> list[str]:
    """The model check before a run; the date text follows the
    label with no space."""
    return [f"Start Pre-Sim Analysis{start or time.ctime()}",
            f"End Pre-Sim Analysis{end or time.ctime()}  -{'Errors' if errors else 'No Errors'} Detected"]


def opened_lines(path_and_name: str) -> list[str]:
    return ["", f"Opened file {path_and_name}"]


def start_simulation_line(today: str | None = None) -> str:
    return f"StartSimulation  {today or time.ctime()}"


def end_simulation_line(today: str | None = None) -> str:
    return f"EndSimulation  {today or time.ctime()}"

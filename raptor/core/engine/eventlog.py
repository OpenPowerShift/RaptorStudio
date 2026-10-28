"""Shared event-log line formatting -- port of `WriteEvents`.

One shared formatter for every object type ("Block", "Node", "Event",
"System", "Pool", "Trigger", "Hier"), matching the source. Checked
byte-for-byte against `tests/golden/Events` in
`tests/unit/test_events_engine.py`.

Each line is a `LogLine`: the formatted text (a `str`, so every report,
writer and test that reads lines is unchanged) that also keeps the five
fields it was formatted from. A run's log is an `EventLog`, a list that
tells its observers about every line added, so a consumer can follow the
run as typed records instead of parsing text.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Self

TAB = "\t"


def _pad(s: str, width: int) -> str:
    """Port of the `SUBSTR(1, width, s + spaces)` pattern in `WriteEvents`
    — left-justify/truncate to exactly `width` characters."""
    return (s + " " * width)[:width]


class LogLine(str):
    """One event-log line: its text, plus the fields it was formatted from.

    Equal to, and written exactly as, the plain text; `time`, `type`,
    `name`, `action` and `comment` are the `WriteEvents` arguments.
    """

    __slots__ = ("action", "comment", "name", "time", "type")
    time: float
    type: str
    name: str
    action: str
    comment: str

    def __new__(cls, text: str, time: float, type_string: str, name_string: str,
                action_string: str, comment_string: str) -> LogLine:
        line = super().__new__(cls, text)
        line.time = time
        line.type = type_string
        line.name = name_string
        line.action = action_string
        line.comment = comment_string
        return line

    def __reduce__(self):
        # Worker processes return their runs' lines to the parent.
        return (LogLine, (str(self), self.time, self.type, self.name, self.action, self.comment))


def format_event_log_line(
    time: float, type_string: str, name_string: str, action_string: str, comment_string: str
) -> LogLine:
    """Port of `WriteEvents`."""
    time_str = _pad(f"{time:.6f}", 16) + "  " + TAB
    type_str = _pad(type_string, 7) + "  " + TAB
    name_str = _pad(name_string, 20) + "  " + TAB
    action_str = _pad(action_string, 15) + "  " + TAB
    return LogLine(time_str + type_str + name_str + action_str + comment_string,
                   time, type_string, name_string, action_string, comment_string)


#: Told about each line added to an `EventLog`.
LogObserver = Callable[[str], None]


class EventLog(list[str]):
    """A run's event log: the lines in order, and the observers told about
    each one as it is added (`append`, `extend`, `+=`, `insert`).

    Lines are usually `LogLine`s; an observer that wants the fields checks
    `isinstance(line, LogLine)`. Observers must not change the log.
    """

    def __init__(self, lines: Iterable[str] = ()) -> None:
        super().__init__(lines)
        self.observers: list[LogObserver] = []

    def subscribe(self, observer: LogObserver) -> None:
        self.observers.append(observer)

    def _notify(self, lines: Iterable[str]) -> None:
        for line in lines:
            for observer in self.observers:
                observer(line)

    def append(self, line: str) -> None:
        super().append(line)
        if self.observers:
            self._notify((line,))

    def insert(self, index, line: str) -> None:
        super().insert(index, line)
        if self.observers:
            self._notify((line,))

    def extend(self, lines: Iterable[str]) -> None:
        lines = list(lines)
        super().extend(lines)
        if self.observers:
            self._notify(lines)

    def __iadd__(self, lines: Iterable[str]) -> Self:  # type: ignore[override,misc]
        # `list.__iadd__` does not go through `extend`, so observers
        # would miss `logs += ...` without this.
        self.extend(lines)
        return self


def event_log_row_prints(type_string: str, name_string: str, *, blocks: bool = False,
                         nodes: bool = False, system: bool = False, phases: bool = False,
                         component: str = "") -> bool:
    """Whether `WriteEvents` writes a row, given the Avant-garde tab's filter
   : `blocks`/`nodes`/`system`/`phases` are its A-D boxes and
    `component` is `detComp`.

    With all four boxes off and no component, **everything prints** (the source's own
    "very awkward" rule). Otherwise a row prints when its category box is on, or its name
    is exactly `component`. `System` rows named `Phase_change` follow box D, other `System`
    rows and `Pool`, `Trigger` and `Hier` rows follow box C (`Begin_Run_n` is a `System`
    row, so it vanishes unless C is on); any other type (`Event`) never prints under a
    filter unless it is the component."""
    # The combo's text is `<name> block - 007`; Raptor 7 keeps only what precedes the
    # first space (read from the source, Raptor 7; the one Raptor 7 capture used a
    # space-free name, so the consequence for a spaced name is not capture-verified).
    component = component.split(" ", 1)[0]
    if not (blocks or nodes or system or phases or component != ""):
        return True
    if component != "" and name_string == component:
        return True
    if type_string == "Block":
        return blocks
    if type_string == "Node":
        return nodes
    if type_string == "System":
        return phases if name_string == "Phase_change" and phases else system
    if type_string in ("Pool", "Trigger", "Hier"):
        return system
    return False


def filter_event_log(lines: Iterable[str], *, blocks: bool = False, nodes: bool = False,
                     system: bool = False, phases: bool = False,
                     component: str = "") -> list[str]:
    """`lines` as Raptor 7 would have written them with that filter. Lines that are
    not `WriteEvents` rows (the blank line and `Started Gathering Statistics` marker, which
    the source writes directly, and any plain text) are kept."""
    if not (blocks or nodes or system or phases or component != ""):
        return list(lines)
    out: list[str] = []
    for line in lines:
        if isinstance(line, LogLine) and not event_log_row_prints(
                line.type, line.name, blocks=blocks, nodes=nodes, system=system,
                phases=phases, component=component):
            continue
        out.append(line)
    return out


def event_log_header(file_name: str, date: str) -> list[str]:
    """The event log file's first three lines (`OpenOutFiles`): the title
    line, then two blank lines."""
    return [f"Event_Log {TAB}for {TAB}{file_name}{TAB} {TAB}{date}", "", ""]


def format_event_log_file(logs: Iterable[Iterable[str]], file_name: str, date: str, *,
                          blocks: bool = False, nodes: bool = False, system: bool = False,
                          phases: bool = False, component: str = "") -> list[str]:
    """The lines of Raptor 7's event log file for a batch: the header, then each run as
    a `Begin_Run_n` System row, its lines, and (between runs) one blank line. `Begin_Run_n`
    is an ordinary `System` row, so a category filter removes it unless box C is on.
    The filter arguments are the Avant-garde tab's (see `event_log_row_prints`)."""
    out = event_log_header(file_name, date)
    for n, run in enumerate(logs, 1):
        if n > 1:
            out.append("")
        marker = format_event_log_line(0.0, "System", f"Begin_Run_{n}", "--", "--")
        out.extend(filter_event_log([marker, *run], blocks=blocks, nodes=nodes, system=system,
                                    phases=phases, component=component))
    return out

#!/usr/bin/env python3
"""
Check the `_pm_race` PM-clock pause/resume/refill rule against this
fixture's captured PM cycle in legacy/rpt_events_PmFailResetOff.txt.

Rule (PMFailReset=0): the PM clock
counts operating time only. It pauses while the block is down (a failure
does not reset it) and refills to `pm_freq` only once decremented to
exactly 0.0 on a genuine PM fire. After a fail/repair cycle that
interrupts an in-progress PM countdown, the next PM fire time is:

    next_pm_due = repair_time + (pm_freq - operating_time_since_last_pm)

`pm_freq` is 100.0 for Attitude (input.rbd BLOCK_DATA row, PMFreq column).

The event log holds many runs concatenated (SimTime resets to 0.0 at each
run boundary). This script anchors on one distinctive instant (the Failed
line with Operating_time=79.427920) and walks strictly forward/backward
from there so it stays within that one run's sequence.

Zero imports from raptor.core: it only reads captured timestamps and does
the subtraction above.

Run: python3 verify_pm_clock.py
"""
import pathlib
import re

HERE = pathlib.Path(__file__).parent
EVENTS = HERE / "legacy" / "rpt_events_PmFailResetOff.txt"
PM_FREQ = 100.0
ANCHOR_OPERATING_TIME = 79.427920
TOL = 1e-5  # display rounds to 6 decimals; arithmetic can round differently in the last place


def parse_lines():
    rows = []
    for line in EVENTS.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            t = float(parts[0].strip())
        except ValueError:
            continue
        rows.append((t, line))
    return rows


def main() -> None:
    rows = parse_lines()

    fail_re = re.compile(r"Attitude\s+Failed\s+Operating_time=([\d.]+)")
    pm_complete_re = re.compile(r"Attitude\s+PM_complete")
    recover_re = re.compile(r"Attitude\s+(Good|End_Post-LDT)")
    pm_due_re = re.compile(r"Attitude\s+PM_is_due")

    anchor_idx = None
    for i, (t, line) in enumerate(rows):
        m = fail_re.search(line)
        if m and abs(float(m.group(1)) - ANCHOR_OPERATING_TIME) < 1e-6:
            anchor_idx = i
            fail_time = t
            operating_time = float(m.group(1))
            break
    if anchor_idx is None:
        raise LookupError(f"no Failed line with Operating_time={ANCHOR_OPERATING_TIME} found")

    # nearest PRECEDING PM_complete (this run's last PM before the failure)
    pm_complete = None
    for t, line in reversed(rows[:anchor_idx]):
        if pm_complete_re.search(line):
            pm_complete = t
            break
    if pm_complete is None or pm_complete > fail_time:
        raise LookupError("couldn't find the preceding PM_complete for this run")

    # sanity: Operating_time= should equal fail_time - pm_complete exactly
    assert abs((fail_time - pm_complete) - operating_time) < TOL, (
        f"Operating_time={operating_time} doesn't match fail_time-pm_complete="
        f"{fail_time - pm_complete}"
    )

    # nearest FOLLOWING recovery, then nearest FOLLOWING PM_is_due
    recover_time = None
    for t, line in rows[anchor_idx + 1:]:
        if recover_re.search(line):
            recover_time = t
            break
    real_next_pm_due = None
    for t, line in rows[anchor_idx + 1:]:
        if t < (recover_time or 0):
            continue
        if pm_due_re.search(line):
            real_next_pm_due = t
            break
    if recover_time is None or real_next_pm_due is None:
        raise LookupError("couldn't find the following recovery/PM_is_due for this run")

    remaining_pm_clock = PM_FREQ - operating_time
    predicted_next_pm_due = recover_time + remaining_pm_clock

    print(f"PM_complete             = {pm_complete}")
    print(f"Failed (Operating_time) = {fail_time}  (Operating_time={operating_time})")
    print(f"remaining PM clock      = {PM_FREQ} - {operating_time} = {remaining_pm_clock}")
    print(f"recovers                = {recover_time}")
    print(f"predicted next PM_is_due = {recover_time} + {remaining_pm_clock} = {predicted_next_pm_due}")
    print(f"real captured PM_is_due  = {real_next_pm_due}")
    diff = abs(predicted_next_pm_due - real_next_pm_due)
    print(f"diff = {diff:.6f}  ({'PASS' if diff < TOL else 'FAIL'})")
    assert diff < TOL, "hand-calc PM clock pause/resume rule does NOT match real capture"


if __name__ == "__main__":
    main()

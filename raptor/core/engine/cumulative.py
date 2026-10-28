"""The "Cumulative By Trial" file (Avant-garde "Cumulative Params by Trial", `RamInterval`):
Raptor 7's header row and `CalculateInterimRams` (a row after every run whose
number is a multiple of the interval).

Each row is the cross-run statistics of the runs finished so far -- the same monitors the Final
Results report is built from (`reports.build_final_results_report`), written as of that run.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from raptor.core.engine.report_format import get_display_str, real_to_sn, write_real
from raptor.core.engine.reporting import RunReportData
from raptor.core.engine.stats import RunningStat

#: Raptor 7, written verbatim.
BY_TRIAL_HEADER = (
    "Trials    Ao           Ao_StDev    Ao_SEM      Do           Do_StDev    Do_SEM      "
    "R            R_StDev     R_SEM       R_SEP       RCond        RCond_StDev RCond_SEM           "
    "MTBDE               MTBDE_StDev          MTBDE_SEM           MDT                 MDT_StDev            "
    "MDT_SEM     MDT_Updates        "
    "MTBM                MTBM_StDev           MTBM_SEM            MMT                 MMT_StDev            "
    "MMT_SEMS    MMT_Updates")

_NA19 = "                N/A "           # `"                N/A "`: 16 spaces, N/A, 1 space


def _format_stats(x: float) -> str:
    from raptor.core.engine.report_format import format_stats
    return format_stats(x)


def _mean(stat: RunningStat) -> float:
    return stat.mean if stat.count else 0.0


def _std(stat: RunningStat) -> float:
    return stat.population_stdev if stat.count else 0.0


def _real(x: float, width: int, dec: int = 9) -> str:
    """`WriteReal(x, width, dec)`: right-justified in `width`."""
    return write_real(x, dec).rjust(width)


def _disp(stat: RunningStat, width: int, key: str) -> str:
    return get_display_str(_std(stat), stat.count, width, 9, 1, key)


def _stats_over(runs: Sequence[RunReportData]) -> dict[str, RunningStat]:
    s = {k: RunningStat() for k in ("avail", "depend", "rel", "rcond", "mtbde", "mdt", "mtbm", "mmt")}
    for r in runs:
        s["avail"].add(r.availability)
        if r.dependability is not None:
            s["depend"].add(r.dependability)
        s["rel"].add(r.reliability)
        if r.rcond_assigned:
            s["rcond"].add(r.conditional_reliability)
        if r.mtbde is not None:
            s["mtbde"].add(r.mtbde)
        if r.mdt is not None:
            s["mdt"].add(r.mdt)
        if r.mtbm is not None:
            s["mtbm"].add(r.mtbm)
        if r.mmt is not None:
            s["mmt"].add(r.mmt)
    return s


def by_trial_row(runs: Sequence[RunReportData]) -> str:
    """The row `CalculateInterimRams` writes after `len(runs)` runs."""
    n = len(runs)
    s = _stats_over(runs)
    out = (str(n) + "          ")[:10]
    for key in ("avail", "depend", "rel"):
        out += _format_stats(_mean(s[key])) + "  "
        out += _disp(s[key], 11, "stdev") + " "
        out += _disp(s[key], 11, "sem") + " "
    r = _mean(s["rel"])
    sep = math.sqrt(r * (1.0 - r) / float(n))
    out += _real(sep, 11) + " "
    out += _format_stats(_mean(s["rcond"])) + "  "
    out += _disp(s["rcond"], 11, "stdev") + " "
    out += _disp(s["rcond"], 11, "sem") + " "
    # MTBDE: StDev/SEM only when more than one sample AND every run had a failure (MDTMon.Count = runs).
    out += _real(_mean(s["mtbde"]), 19) if s["mtbde"].count > 0 else _NA19
    if s["mtbde"].count > 1 and s["mdt"].count == n:
        out += " " + _disp(s["mtbde"], 19, "stdev") + " " + " " + _disp(s["mtbde"], 19, "sem") + " "
    else:
        out += _NA19 + _NA19
    out += _real(_mean(s["mdt"]), 19) if s["mdt"].count > 0 else _NA19
    if s["mdt"].count > 1:
        out += " " + _disp(s["mdt"], 19, "stdev") + " " + " " + _disp(s["mdt"], 19, "sem") + " "
    else:
        out += _NA19 + _NA19
    out += "  " + str(s["mdt"].count).rjust(9)
    out += _real(_mean(s["mtbm"]), 19) if s["mtbm"].count > 0 else _NA19
    if s["mtbm"].count > 1 and s["mmt"].count == n:
        out += " " + _disp(s["mtbm"], 19, "stdev") + " " + " " + _disp(s["mtbm"], 19, "sem") + " "
    else:
        out += _NA19 + _NA19
    out += _real(_mean(s["mmt"]), 19) if s["mmt"].count > 0 else _NA19
    if s["mmt"].count > 1:
        out += " " + _disp(s["mmt"], 19, "stdev") + " " + " " + _disp(s["mmt"], 19, "sem") + " "
    else:
        out += _NA19 + _NA19
    out += "  " + str(s["mmt"].count).rjust(9)
    return out


def by_trial_lines(runs: Sequence[RunReportData], interval: int) -> list[str]:
    """The whole file: the header, then a row after each run that is a multiple of `interval`."""
    lines = [BY_TRIAL_HEADER]
    if interval > 0:
        for n in range(interval, len(runs) + 1, interval):
            lines.append(by_trial_row(runs[:n]))
    return lines


#: Raptor 7 (`WriteCumParamFile`), written verbatim; the line ends with `Trials=` and
#: the run count right-justified in 12.
BY_TIME_HEADER = (
    "SimTime        Ao            Ao_StDev      Ao_SEM        MTBDE           MTBDE_StDev     "
    "MTBDE_SEM       MDT             MDT_StDev       MDT_SEM       MDT_Updates          "
    "MTBM            MTBM_StDev      MTBM_SEM        MMT             MMT_StDev       MMT_SEM       "
    "MMT_Updates   R           R_StDev     R_SEM       R_SEP        Trials=")

_NA14 = "N/A           "
_NA16 = "N/A             "


class CumulativeByTime:
    """The "Cumulative By Time" file (Avant-garde "Cumulative Params by Time", `rMesh`): at every
    multiple of `mesh` of each run a snapshot of the run's own cumulative figures goes into one
    monitor per interval (`UpdateCumulativeValues`), and after the last run
    the monitors' cross-run statistics are written (`WriteCumParamFile`).

    `reliable` is `reliabilityFileData` (`RecordReliabilityFileData`): how many runs were
    still free of a system failure at each mesh point. Time-terminated runs only, as in the source.
    """

    def __init__(self, sim_length: float, mesh: float, num_runs: int) -> None:
        self.sim_length = sim_length
        self.mesh = mesh
        self.num_runs = num_runs
        n = int(sim_length / mesh)                      # TRUNC(simLength/kpfTimeMesh)
        self.n = n
        self.ao = [RunningStat() for _ in range(n + 1)]       # 1-based, like the source's arrays
        self.mtbde = [RunningStat() for _ in range(n + 1)]
        self.mdt = [RunningStat() for _ in range(n + 1)]
        self.mtbm = [RunningStat() for _ in range(n + 1)]
        self.mmt = [RunningStat() for _ in range(n + 1)]
        self.reliable = [0] * (n + 1)                   # 0..n

    def tick(self, i: int, state, sim_time: float) -> None:
        """Interval `i` of the current run, at `sim_time` ."""
        from raptor.core.engine.system_status import SystemColor
        if i > self.n:
            return
        green, yellow, red = state.green_stat.sum, state.yellow_stat.sum, state.red_stat.sum
        failures = state.red_stat.count
        left = sim_time - state.last_color_change
        if state.color == SystemColor.GREEN:
            green += left
        elif state.color == SystemColor.YELLOW:
            yellow += left
        elif state.color == SystemColor.RED:
            red += left
            failures += 1
        up = green + yellow
        self.ao[i].add(up / (up + red) if (up + red) > 0.0 else 1.0)
        self.mtbde[i].add(up / float(failures) if failures > 0 else up)
        if failures > 0:
            self.mdt[i].add(red / float(failures))
        fails, pms = state.comp_fail_stat.count, state.comp_pm_stat.count
        if up > 0.0:
            if fails > 0 and pms > 0:
                self.mtbm[i].add(up / (float(fails) + float(pms)))
            elif pms > 0:
                self.mtbm[i].add(up / float(pms))
            elif fails > 0:
                self.mtbm[i].add(up / float(fails))
            else:
                self.mtbm[i].add(up)
        repairs = state.comp_repair_stat
        if repairs.count > 0 and pms > 0:
            self.mmt[i].add((repairs.mean * float(repairs.count)
                             + state.comp_pm_stat.mean * float(pms)) / float(repairs.count + pms))
        elif repairs.count > 0:
            self.mmt[i].add(repairs.mean)
        elif pms > 0:
            self.mmt[i].add(state.comp_pm_stat.mean)

    def record_reliability(self, sim_time: float, *, at_end: bool) -> None:
        """`RecordReliabilityFileData`: a run that fails (`at_end=False`, at the failure time) or is
        still unfailed when it ends (`at_end=True`, at the run length) was reliable up to there."""
        stop = self.sim_length if at_end else sim_time
        upto = int((stop + 0.0000001) / self.mesh)
        for k in range(0, min(upto, self.n) + 1):
            self.reliable[k] += 1

    def lines(self) -> list[str]:
        out = [BY_TIME_HEADER + str(self.num_runs).rjust(12)]
        nr = self.num_runs
        for i in range(1, self.n + 1):
            ao, tbde, mdt, tbm, mmt = self.ao[i], self.mtbde[i], self.mdt[i], self.mtbm[i], self.mmt[i]
            row = real_to_sn(float(i) * self.mesh, 15, 4, 0)
            row += real_to_sn(_mean(ao), 14, 9, 0)
            if ao.count > 1:
                sigma = _sample(ao)
                row += real_to_sn(sigma, 14, 9, 0) + real_to_sn(sigma / math.sqrt(float(nr)), 14, 9, 0)
            else:
                row += _NA14 * 2
            row += real_to_sn(_mean(tbde), 16, 6, 0)
            if tbde.count > 1 and mdt.count == nr:
                sigma = _sample(tbde)
                row += real_to_sn(sigma, 16, 6, 0) + real_to_sn(sigma / math.sqrt(float(nr)), 16, 6, 0)
            else:
                row += _NA16 * 2
            row += real_to_sn(_mean(mdt), 16, 6, 0)
            if mdt.count > 1:
                sigma = _sample(mdt)
                row += real_to_sn(sigma, 16, 6, 0) + real_to_sn(sigma / math.sqrt(float(mdt.count)), 16, 6, 0)
            else:
                row += _NA16 * 2
            row += str(mdt.count).rjust(9) + "          "
            row += real_to_sn(_mean(tbm), 16, 6, 0)
            if tbm.count > 1 and mmt.count == nr:
                sigma = _sample(tbm)
                row += real_to_sn(sigma, 16, 6, 0) + real_to_sn(sigma / math.sqrt(float(nr)), 16, 6, 0)
            else:
                row += _NA16 * 2
            row += real_to_sn(_mean(mmt), 16, 6, 0)
            if mmt.count > 1:
                sigma = _sample(mmt)
                row += real_to_sn(sigma, 16, 6, 0) + real_to_sn(sigma / math.sqrt(float(mmt.count)), 16, 6, 0)
            else:
                row += _NA16 * 2
            row += str(mmt.count).rjust(9) + "   "
            successes = float(self.reliable[i])
            trials = float(nr)
            r = successes / trials
            row += real_to_sn(r, 12, 9, 0)
            if nr > 1:
                sigma = math.sqrt(max((trials * successes - successes * successes) / (trials * (trials - 1.0)), 0.0))
            else:
                sigma = 0.0
            row += real_to_sn(sigma, 12, 9, 0)
            row += real_to_sn(sigma / math.sqrt(trials), 12, 9, 0)
            row += real_to_sn(math.sqrt(r * (1.0 - r) / trials), 12, 9, 0)
            out.append(row)
        return out


def _sample(stat: RunningStat) -> float:
    """`PopToSample(StdDev, Count)`."""
    from raptor.core.engine.report_format import pop_to_sample
    return pop_to_sample(_std(stat), stat.count)

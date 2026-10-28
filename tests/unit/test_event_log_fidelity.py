"""Event-log fidelity guard: every fixture listed here reproduces its real
Raptor7Student `rpt_events*.txt` capture byte-for-byte (first five
tab-separated columns per line, `Begin_Run` markers dropped), every run.

Why this exists (2026-09-23): the other golden tiers compare RNG draws
(`sd_diff`) and End-of-Run / Final Results reports (`test_golden_fidelity`),
so event-log TEXT was unchecked for most fixtures -- 880 wrong
`DependenceStress=` lines in Phasing1 survived with every test green, and
two engine changes (769c9cf, 562da8a) silently CRASHED six ReleaseProbes for
weeks because their captures had never been filed. `tools/
event_log_scan.py` is the exploratory, whole-corpus version of this; this
file is the regression guard for every fixture that is fully
byte-exact (98 when written, 103 by the end of 2026-09-23). A fixture joins the list deliberately once
it goes byte-exact; it may only leave it with a stated, evidenced reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.unit.log_compare import read_capture_text

from raptor.core.engine.runner import run_simulation
from raptor.core.fileio.builder import load

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "tools"))
import golden_fixtures as gf  # noqa: E402

GOLDEN = _REPO / "tests" / "golden"

BYTE_EXACT_FIXTURES = [
    "AntitheticProbe",
    "BlockDepDownPerc",
    "CSColdPmProbe",
    "CSExcessSwitchProbe",
    "CSSwitchRaceProbe",
    "CS_DepOnStandbyPeer",
    "CS_HigherCold",
    "CS_MidSwitchDemote",
    "CS_NoAutosFirst",
    "CS_Probe",
    "CS_Probe_AProb099",
    "CS_Probe_AProb1",
    "CS_Probe_cold",
    "CS_Probe_cold_noLDT",
    "CS_Probe_hot",
    "CS_Probe_pure",
    "CS_SwitchFail",
    "CUST_ALL",
    "CYCLESIM",
    "CYCLESIM_SS",
    "Capacity1",
    "Capacity2",
    "Capacity3",
    "Ch07Dependent",
    "ColdEventProbe",
    "ColdPhasedKStarProbe",
    "ColdPriorityTieProbe",
    "ColdStandbyProbe2",
    "ComplexExample",
    "ComplexModel",
    "CustExhaust",
    "CutRepairIdleProbe",
    "CutRepairProbe",
    "CycleSimStartStatsCapacityProbe",
    "DEPDOWN",
    "DepBlockDoneProbe",
    "DepBlockPmObpmProbe",
    "DepCancelConservationProbe",
    "DepCustomESOProbe",
    "DepCycle3Probe",
    "DepCycleFailProbe",
    "DepCycleIdleProbe",
    "DepCycleMixedProbe",
    "DepCyclePmProbe",
    "DepCycleSysProbe",
    "DepCycleTieProbe",
    "DepHierProbe",
    "DepPM",
    "DepPmBeyondHorizonProbe",
    "DepPmFarWaitProbe",
    "DependentStressProbe",
    "DistributionProbe2",
    "ESOCustom",
    "EmpiricalProbe",
    "EventDepProbe",
    "Events",
    "F2_Pearson5Probe",
    "F2_WeibullProbe",
    "FactoryDemo",
    "FailSimOutOfSparesProbe",
    "FreqPMProbe",
    "GDTypeProbe",
    "GDTypeProbe2",
    "HierDegradeProbe",
    "HierarchyExample",
    "InterruptTellCollideProbe",
    "LadL1Busy",
    "LadL1Ldt",
    "LadL1Parallel",
    "LadL1Series",
    "LadL2Both",
    "LadL2Pool",
    "LadL2Res",
    "LadL3Mix",
    "LadL3Periodic",
    "LadL3Trig",
    "LadL4Block",
    "LadL4Node",
    "LadL4Web",
    "LadL5Cold",
    "LadL5ColdBusy",
    "LadL5Full",
    "LadL6Cycles",
    "LadL6Full",
    "LadL6Time",
    "LadL7Cap",
    "LadL7CapMix",
    "LadL7Cost",
    "LadL7Gd",
    "LadL7Hier",
    "LadL7HierFull",
    "LadL7Weak",
    "LadL8A",
    "LadL8BEvents",
    "LadL8CEvents",
    "LargeShape1000Probe",
    "LinkRenumberProbe",
    "MixedDepPerc",
    "MotorDemo",
    "MultiOutLink",
    "NoRepairCheckForSparePooledProbe",
    "NoRepairCheckForSpareProbe",
    "NodeStandbyBackflowProbe",
    "NodeStandbyProbe",
    "PMCustomSLOProbe",
    "PMCustomSLOResourceDeferProbe",
    "PMGrantVsRepairSpareProbe",
    "PMStaggerProbe",
    "PMStressProbe",
    "PM_Prereq",
    "PhaseBarrierProbe",
    "PhaseFailTieProbe",
    "PhaseLinkedStartProbe",
    "PhasedFailSimOrderProbe",
    "PhasedWeakAnalysisMultiMissionProbe",
    "Phasing1",
    "Phasing3",
    "PhaseIns3",
    "PieFacility2",
    "PmCustomESOProbe",
    "PmFailResetOff",
    "PmHoldProbe",
    "PmHoldSloProbe",
    "PmMisDeferBoundaryProbe",
    "PmMisDeferGiveBackProbe",
    "PmMisDeferProbe",
    "PmMisDeferTimerProbe",
    "PoolExhaustDoneProbe",
    "PoolSloGrabProbe",
    "PoolStockProbe",
    "PooledGraceRepairProbe",
    "PooledLDTOrderProbe",
    "PreventMxDefer",
    "PreventMxDemo",
    "PreventMxNoDefer",
    "ReleaseProbe",
    "ReleaseProbe15",
    "ReleaseProbe16",
    "ReleaseProbe17",
    "ReleaseProbe18",
    "ReleaseProbe19",
    "ReleaseProbe2",
    "ReleaseProbe20",
    "ReleaseProbe21",
    "ReleaseProbe22",
    "ReleaseProbe23",
    "ReleaseProbe24",
    "ReleaseProbe25",
    "ReleaseProbe26",
    "ReleaseProbe27",
    "ReleaseProbe28",
    "ReleaseProbe29",
    "ReleaseProbe3",
    "ReleaseProbe30",
    "ReleaseProbe31",
    "ReleaseProbe32",
    "ReleaseProbe33",
    "ReleaseProbe34",
    "ReleaseProbe35",
    "ReleaseProbe36",
    "ReleaseProbe37",
    "ReleaseProbe38",
    "ReleaseProbe39",
    "ReleaseProbe4",
    "ReleaseProbe40",
    "ReleaseProbe41",
    "ReleaseProbe42",
    "ReleaseProbe43",
    "ReleaseProbe44",
    "ReleaseProbe5",
    "ResourcePool",
    "ResourceRepairOrderProbe",
    "ResourceRepairOrderProbe2",
    "SLOProbe",
    "SLOTest2",
    "SPFProbe",
    "SharedStreamTie",
    "SimStartLdtProbe",
    "SimStartProbe",
    "SimStartProbe2",
    "SimStartProbe3",
    "SingleHierarchy",
    "SpaceStation",
    "SparingNoneProbe",
    "StartupFailuresProbe",
    "TieOrderProbe",
    "TriggerOrderProbe",
    "TriggerWakeOrder",
    "WakeOrderDependent",
    "WakeOrderInduced",
    "WeakAnalysisProbe",
    "ZeroWaitReasonsProbe",
    "ZeroFailFailSimProbe",
    "ZeroFailTimeProbe",
    "IsolatePlain",
    "IsolateDeps",
    "IsolateShare",
    "IsolateCold",
]


def _rows(lines):
    return [tuple(c.strip() for c in l.split("\t")[:5]) for l in lines if "\t" in l]


@pytest.mark.parametrize("fixture", BYTE_EXACT_FIXTURES)
def test_event_log_matches_real_capture(fixture: str) -> None:
    capture = gf.single_match(GOLDEN / fixture / "legacy", "rpt_events*.txt")
    rbd = gf.resolve_rbd_path(fixture)
    assert capture is not None and rbd is not None, fixture
    # Keyed by the number in `Begin_Run_N` (see event_log_scan.py): a
    # capture whose head was overwritten (PmFailResetOff) starts at
    # `Begin_Run_2`, and its leading partial run is skipped.
    real_by_num: dict[int, list] = {}
    cur = None
    for r in _rows(read_capture_text(capture).splitlines()[1:]):
        if r[2].startswith("Begin_Run"):
            cur = real_by_num.setdefault(int(r[2].rsplit("_", 1)[1]), [])
            continue
        if cur is not None:
            cur.append(r)
    nums = sorted(real_by_num)
    # A capture made with Interim Costs on (an `rpt_interim_cost_*` sibling)
    # is compared with a cost sink -- that option changes real's results
    # (see event_log_scan.py / driver.py's module docstring).
    cost_on = bool(list((GOLDEN / fixture / "legacy").glob("rpt_interim_cost*.txt")))
    ours = [[r for r in _rows(run) if not r[2].startswith("Begin_Run")]
            for run in run_simulation(load(str(rbd)), num_runs=max(nums),
                                      _cost_sink=[] if cost_on else None)]
    for n in nums:
        o, r = ours[n - 1], real_by_num[n]
        if o != r:
            j = next((k for k in range(min(len(o), len(r))) if o[k] != r[k]), min(len(o), len(r)))
            pytest.fail(f"{fixture} run {n} line {j}: ours={o[j] if j < len(o) else None} "
                        f"real={r[j] if j < len(r) else None}")

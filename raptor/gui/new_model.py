"""Blank-model template for File > New.

A `.rbd` file is only valid if its sections and columns match what the
parser expects: the mandatory ID=0 BLOCK_DATA template row, END markers,
count lines, and so on. This module embeds a minimal known-good file
text, modeled on the structure of `tests/golden/UniformProbe/input.rbd`
(one Exponential-100h / Fixed-2h block wired Start -> End), and lets
`parse_rbd` construct the object, so whatever survives parsing is
loadable, savable and runnable.

`NEW_MODEL_TEXT` is a RUNTIME DEPENDENCY, not a fixture
-------------------------------------------------------
`raptor/core/fileio/rbds.py:311` imports it and parses it to obtain the
base a `.rbds` read merges into: the `ID=0` block template row, the
default link row, every section's COLUMN LIST, and the complete
27-column `SYSTEM_DATA` row. The run settings are therefore NOT stored
here as values -- they are applied on top, by `build_new_model`, from
`RunDefaults`. Stripping the numbers out of the embedded string instead
would break every `.rbds` read in the application.
"""

NEW_MODEL_TEXT = """\
Version7_7.0.11 Student        NewModel.rbd        Thu Jan 01 00:00:00 2026
Blocks=        1
Events=        0
Nodes=        2
Links=        0
SpPools=      0
ResPools=     0
Phases=       0
Triggers=     0
Hierarchies=  0
/
/___SYSTEM_DATA___
imageString TerminationType TermCondition StartStats NumRuns graphTimeSlice graphYmin graphYmax zoomVal units graphicsUse SysStreamsArray xOrigin yOrigin ShowNegErrMsg IgnoreStartFail flowGenerated systemRedCost sysLostCost weakAnalysis costAnalysis capacityAnalysis nodeAnalType GYthreshold YRthreshold NotUsed
Bull's_eye TimeSim 100.000000 0.000000 1.000000 100000.000000 0.000000 1.000000 24.000000 hours GraphicsOn 7 8 9 10 70 71 72 73 74 75 101 0.000000 80.000000 0 0 2050 13300.000000 6.480000 0 0 0 Availability 0.950000 0.900000 1
EndOfSystemData
/___HIERARCHY_DATA___
hierarchyName ID xPos yPos Parent In Out isCNd zoom xCenter yCenter in out level Phs xOrigin yOrigin myDepth
EndOfHierarchyData
/___BLOCK_DATA___
blockName ID xPos yPos ParentId In Out Phasing SimStart AmtExhausted FDist #FParam FStream FParam1 FParam2 FParam3 RDist #RParam RStream RParam1 RParam2 RParam3 SBStress GDType GDRate GDLimit SparePoolName SpType RSO Stock NewSp ArriveEvery ESO EmerTime SLO Level Quan SLOTime Pre-LDTDist Param1 Param2 Param3 Post-LDTDist Param1 Param2 Param3 NumRes #Res1Rep #Res1pmRep Resource1Name usesPM PMSpare PMRefresh PMMisDefer PMFailReset PMReqDefer PMStaggerTime PMFreq pmTriggered pmTrig Dist Param1 Param2 Param3 DepNum DepNothing DepIdle DepPM DepFail DepPMThresh defDepStateIdle DepType initial Running standby idle repHold repPerTime repFixed donePerTime doneFixed pmHoldCost pmCost pmFixedCost perSpare emerShipping addDone
unnamed 0 0.000000 0.000000 0 0 0 0 3 0.000000 4 2 203 100.000000 0.000000 0.000000 7 2 203 10.000000 2.000000 0.000000 0.000 0 1.000000 0.500000 unnamed Infinite 0 100 1 720.000000 0 24.000000 0 0 1 24.000000 19 0.000000 0.000000 0.000000 19 0.000000 0.000000 0.000000 0 1 1 None 0 0 0 0 0 0 0.000000 100.000000 0 none 19 1.000000 0.000000 0.000000 0 0.00 100.00 0.00 0.00 75.00 0 None 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 0
Block_1 1 50.000000 91.250000 0 0 0 0 3 0.000000 4 2 203 100.000000 0.000000 0.000000 7 2 203 10.000000 2.000000 0.000000 0.000 0 1.000000 0.500000 unnamed Infinite 0 100 1 720.000000 0 24.000000 0 0 1 24.000000 19 0.000000 0.000000 0.000000 19 0.000000 0.000000 0.000000 0 1 1 None 0 0 0 0 0 0 0.000000 100.000000 0 none 19 1.000000 0.000000 0.000000 0 0.00 100.00 0.00 0.00 75.00 0 None 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 1.00 0
EndOfBlockData
/___EVENT_DATA___
eventName ID xPos yPos ParentId In Out Phasing Distro failStream failVal InitCost OpCost RepCost
EndOfEventData
/___SPARE_POOL_DATA___
PoolName RSO Stock NewSp ArriveEvery ESO EmerTime SLO Level Quan SLOTime SpCost emerShipCost
EndOfSparePoolData
/___RESOURCE_DATA___
PoolName NumIn SpareCost FixedPerUse PerTimeUnit Phases
EndOfResourceData
/___PHASES___
PhaseName Num Mission Dist Param1 Param2 Param3
EndOfPhasesData
/___TRIGGERS___
TriggerName IDnum Repeats InitUsed Dist Param1 Param2 Param3
EndOfTriggerData
/___NODE_DATA___
Name ID xPos yPos parent Type k n Out Cold PR Autos k* Phases depNum depType divFlow fullflow repCap repNA
start 1 5.000000 91.250000 0 1 0 0 1 0 0 0 0 0 0 None 1 1 0 0
end 2 95.000000 91.250000 0 3 -1 1 0 0 0 0 0 0 0 None 1 1 0 0
EndOfNodeData
/___LINK_DATA___
ID FromType FromID ToType ToID pID ColdPri AProb AutoTime ManTime CapPri nomFlow MaxFlow
1 Node 1 Block 1 0 0 1.000000 0.000000 0.000000 0 100.000000 100.000000
2 Block 1 Node 2 0 0 1.000000 0.000000 0.000000 0 100.000000 100.000000
EndOfLinkData
/_______________________COMMENTS____________________________________________________________________________________________
Type      ID  Name                 Comment
/
EndOfFile
"""


#: What a NEW model starts with, in one documented place.
#:
#: The embedded template above is the SKELETON (sections, columns, the
#: ID=0 block row) and its SYSTEM_DATA values are placeholders; these
#: are the values a user actually gets. They are kept here rather than
#: inline in the ASCII text because they need a comment, they need to
#: agree with `core/run_experiment.RUN_DEFAULTS`, and they are the thing
#: a reader of the code should be able to check.
#:
#: The run length is 1000 to match the ORIGINAL Raptor's default
#: the template said 100, a silent 10x
#: difference from the tool this ports.
NEW_MODEL_DEFAULTS: dict = {
    "NumRuns": "1",
    "TerminationType": "TimeSim",
    "TermCondition": "1000.000000",
    "StartStats": "0.000000",
    "units": "hours",
    "GraphicsOn": "GraphicsOn",
    "GYthreshold": "0.950000",
    "YRthreshold": "0.900000",
    "weakAnalysis": "0",
    "costAnalysis": "0",
    "capacityAnalysis": "0",
}


def build_new_model(file_name: str = "NewModel", *,
                    experiment: dict | None = None,
                    units: str | None = None,
                    profile: str | None = None):
    """Parse the embedded template into a fresh `RbdFile`, then apply the
    run defaults on top.

    `experiment` / `profile` / `units` come from the Preferences dialog's
    "New models" tab, so "what a new model starts with" is one setting
    the user controls rather than a constant buried in a string.

    `raptor.core.fileio.rbds` also parses this template (for its column
    skeleton), and it passes neither -- so the fallbacks below have to be
    the real defaults, not None."""
    from raptor.core.fileio.rbd import parse_rbd
    from raptor.core import run_experiment

    rbd = parse_rbd(NEW_MODEL_TEXT.replace(
        "NewModel.rbd        Thu Jan 01 00:00:00 2026",
        f"{file_name}.rbd        Thu Jan 01 00:00:00 2026"))

    sec = rbd.sections.get("SYSTEM_DATA")
    if sec and sec.rows:
        row = sec.rows[0]
        for col, val in NEW_MODEL_DEFAULTS.items():
            if col in row:
                row[col] = val
        if units:
            row["units"] = units
        # The experiment is the single writer for trials/termination/
        # analyses, so the row and the experiment block cannot disagree.
        profile_experiment = (run_experiment.apply_profile(profile, {})
               if profile else run_experiment.defaults())
        if experiment:
            profile_experiment = run_experiment.normalise(experiment)
        for col, val in run_experiment.to_system_row(profile_experiment):
            if col in row:
                row[col] = val
        rbd.experiment = profile_experiment
    return rbd

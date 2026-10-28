"""Row constructors for building Raptor 7 ``.rbd`` files programmatically.

Produces files in the exact legacy text format, using
``raptor.core.fileio.rbd``'s RbdFile/RbdSection containers and
``render_rbd()`` for serialization.  Column layouts mirror the Raptor 7 files
(``tests/golden/*/input.rbd``).

This module lives inside the installed ``raptor`` package so the API and
MCP layers can import it without the repository on ``sys.path``.  The
historical ``legacy/rbd/library/rbdlib.py`` path is kept as a thin re-export
shim for the many dev scripts that do ``from rbdlib import *``.
"""
from __future__ import annotations

from raptor.core.fileio.rbd import RbdFile, RbdSection, render_rbd

# ---- column layouts (exact, from Raptor 7 fixtures) ---------------------------

SYSTEM_COLS = ("imageString TerminationType TermCondition StartStats NumRuns "
               "graphTimeSlice graphYmin graphYmax zoomVal units graphicsUse "
               "SysStreamsArray xOrigin yOrigin ShowNegErrMsg IgnoreStartFail "
               "flowGenerated systemRedCost sysLostCost weakAnalysis costAnalysis "
               "capacityAnalysis nodeAnalType GYthreshold YRthreshold NotUsed").split()

HIER_COLS = ("hierarchyName ID xPos yPos Parent In Out isCNd zoom xCenter yCenter "
             "in out level Phs xOrigin yOrigin myDepth extraInt1 extraInt2 "
             "extraReal1 extraReal2").split()

BLOCK_COLS = ("blockName ID xPos yPos ParentId In Out Phasing SimStart AmtExhausted "
              "FDist #FParam FStream FParam1 FParam2 FParam3 "
              "RDist #RParam RStream RParam1 RParam2 RParam3 "
              "SBStress GDType GDRate GDLimit SparePoolName SpType RSO Stock NewSp "
              "ArriveEvery ESO EmerTime SLO Level Quan SLOTime "
              "Pre-LDTDist Param1 Param2 Param3 Post-LDTDist Param1 Param2 Param3 "
              "NumRes #Res1Rep #Res1pmRep Resource1Name "
              "usesPM PMSpare PMRefresh PMMisDefer PMFailReset PMReqDefer "
              "PMStaggerTime PMFreq pmTriggered pmTrig Dist Param1 Param2 Param3 "
              "DepNum DepNothing DepIdle DepPM DepFail DepPMThresh defDepStateIdle DepType "
              "initial Running standby idle repHold repPerTime repFixed donePerTime "
              "doneFixed pmHoldCost pmCost pmFixedCost perSpare emerShipping addDone").split()

EVENT_COLS = ("eventName ID xPos yPos ParentId In Out Phasing Distro failStream "
              "failVal InitCost OpCost RepCost").split()

NODE_COLS = ("Name ID xPos yPos parent Type k n Out Cold PR Autos k* Phases "
             "depNum depType divFlow fullflow repCap repNA").split()

COMMENT_COLS = "Type ID Name Comment".split()

LINK_COLS = ("ID FromType FromID ToType ToID pID ColdPri AProb AutoTime ManTime "
             "CapPri nomFlow MaxFlow").split()

SPARE_COLS = ("PoolName RSO Stock NewSp ArriveEvery ESO EmerTime SLO Level Quan "
              "SLOTime SpCost emerShipCost").split()

RES_COLS = "PoolName NumIn SpareCost FixedPerUse PerTimeUnit Phases".split()

PHASE_COLS = "PhaseName Num Mission Dist Param1 Param2 Param3".split()

TRIGGER_COLS = "TriggerName IDnum Repeats InitUsed Dist Param1 Param2 Param3".split()

# distribution IDs (from Raptor 7's DrawNumber)
D_BETA, D_CHI2, D_BINOM, D_EXPO, D_ERLANG, D_GAMMA, D_LOGNORM, D_NORM = 1, 2, 3, 4, 5, 6, 7, 8
D_UINT, D_UREAL, D_P5, D_P6, D_POISSON, D_TRIANG, D_WEIBULL, D_EMPIRICAL = 9, 10, 11, 12, 13, 14, 15, 16
D_BERNOULLI, D_NONE, D_FIXED, D_ERROR, D_EXTREME, D_LAPLACE = 17, 18, 19, 20, 21, 22

DIST_NPARAMS = {1: 2, 2: 1, 3: 2, 4: 2, 5: 2, 6: 3, 7: 2, 8: 2, 9: 2, 10: 2,
                11: 3, 12: 3, 13: 1, 14: 3, 15: 3, 17: 1, 18: 0, 19: 1, 20: 3, 21: 2, 22: 2}

#: param-count override when the on-disk convention differs from the dist's
#: logical arity (None repair is written as `#RParam=1, RParam1=1.0` in real
#: fixtures).
DIST_NPARAMS_DISK = {18: 1}
DIST_DISK_PAD = {18: (1.0, 0.0, 0.0)}

SYS_STREAM_C = 203  # system stream C marker used in FStream/RStream/failStream


def fnum(x) -> str:
    """Format numbers the way the legacy writer does (6 dp)."""
    if isinstance(x, str):
        return x
    if isinstance(x, int):
        return str(x)
    return f"{x:.6f}"


def make_system_row(term_type="TimeSim", term=730.5, runs=10, start_stats=0.0,
           image="Bull's_eye", units="hours", graphics="GraphicsOn",
           flow=1, redcost=0.0, lostcost=0.0, weak=0, cost=0, cap=0,
           analtype="Availability", gtslice=100000.0, ymin=0.0, ymax=1.0,
           zoom=24.0, streams="7 8 9 10 70 71 72 73 74 75 101",
           negmsg=0, ignorestart=0, gy=0.95, yr=0.90, notused=1):
    return dict(zip(SYSTEM_COLS, [
        image, term_type, fnum(term), fnum(start_stats), fnum(float(runs)),
        fnum(gtslice), fnum(ymin), fnum(ymax), fnum(zoom), units, graphics,
        streams, "0.000000", "80.000000", str(negmsg), str(ignorestart),
        str(flow), fnum(redcost), fnum(lostcost), str(weak), str(cost), str(cap),
        analtype, fnum(gy), fnum(yr), str(notused)]))


def dist_param_count(dist) -> int:
    """How many parameters this distribution declares on disk.

    ONE owner for the arity. `api/build.py` and `gui/ui/diagram.py` both
    write `#FParam`/`#RParam`, and the canvas used to hard-code `"2" if
    fdist == 4` -- two params for every non-Exponential distribution --
    while `DIST_NPARAMS` says Gamma (6), Pearson5 (11), Pearson6 (12),
    Triangular (14) and Weibull (15) take three. A Weibull placed from the
    canvas therefore wrote `#FParam = 2`, and the Raptor 7 engine read the
    location slot out of bounds.

    Defaults to 3, the widest shape the row has room for, so an unknown or
    absent code writes a count the row can hold rather than one that
    truncates.
    """
    return DIST_NPARAMS_DISK.get(dist, DIST_NPARAMS.get(dist, 3))


def _dist_disk(dist, params):
    """Return (#params_on_disk, (p1, p2, p3)) exactly as legacy writer emits."""
    if dist == D_EMPIRICAL:
        # legacy writer: 3 zero placeholders on the row; data points go on a
        # continuation line; #params = number of data points.
        return len(params), (0.0, 0.0, 0.0)
    if dist in DIST_NPARAMS_DISK:
        n = DIST_NPARAMS_DISK[dist]
        pad = DIST_DISK_PAD.get(dist, (0.0, 0.0, 0.0))
        return n, pad
    n = DIST_NPARAMS.get(dist, len(params))
    p = list(params) + [0.0] * (3 - len(params))
    return n, tuple(p[:3])


def make_block_row(bid, name="unnamed", x=0.0, y=0.0, parent=0, nlinks=(1, 1),
             fdist=D_EXPO, fparams=(100.0, 0.0, 0.0), fstream=SYS_STREAM_C,
             rdist=D_LOGNORM, rparams=(10.0, 2.0, 0.0), rstream=SYS_STREAM_C,
             simstart=3, amtexh=0.0, phasing=0, sbstress=0.0,
             gdtype=0, gdrate=1.0, gdlimit=1.0,
             pool="unnamed", sptype="Infinite",
             rso=1, stock=100, newsp=1, arrive=720.0, eso=1, emertime=24.0,
             slo=0, level=0, quan=1, slotime=24.0,
             predist=D_FIXED, preparams=(0.0, 0.0, 0.0),
             postdist=D_FIXED, postparams=(0.0, 0.0, 0.0),
             numres=0, res1rep=1, res1pm=1, res1name="None",
             usespm=0, pmspare=1, pmrefresh=1, pmmisdefer=0, pmfailreset=1,
             pmreqdefer=1, pmstagger=0.0, pmfreq=100.0, pmtriggered=0,
             pmtrig="none", pmdist=D_FIXED, pmparams=(1.0, 0.0, 0.0),
             depnum=0, depnothing=0.0, depidle=100.0, deppm=0.0, depfail=0.0,
             depthresh=75.0, defidle=0, deptype="None",
             costs=(1.0,) * 14, adddone=0, phases=None):
    """costs = (initial, Running, standby, idle, repHold, repPerTime, repFixed,
                donePerTime, doneFixed, pmHoldCost, pmCost, pmFixedCost,
                perSpare, emerShipping)
    phases = list of per-phase values e.g. ["A:   1.000", "C:   0.500"] or None.
    """
    nfp, fp = _dist_disk(fdist, fparams)
    nrp, rp = _dist_disk(rdist, rparams)
    _npm, pp = _dist_disk(pmdist, pmparams)
    _npre, pre = _dist_disk(predist, preparams)
    _npost, post = _dist_disk(postdist, postparams)
    vals = [name, str(bid), fnum(x), fnum(y), str(parent), str(nlinks[0]),
            str(nlinks[1]), str(phasing),
            str(simstart), fnum(amtexh),
            str(fdist), str(nfp), str(fstream), fnum(fp[0]), fnum(fp[1]), fnum(fp[2]),
            str(rdist), str(nrp), str(rstream), fnum(rp[0]), fnum(rp[1]), fnum(rp[2]),
            f"{sbstress:.3f}", str(gdtype), fnum(gdrate), fnum(gdlimit),
            pool, sptype, str(rso), str(stock), str(newsp), fnum(arrive),
            str(eso), fnum(emertime), str(slo), str(level), str(quan), fnum(slotime),
            str(predist), fnum(pre[0]), fnum(pre[1]), fnum(pre[2]),
            str(postdist), fnum(post[0]), fnum(post[1]), fnum(post[2]),
            str(numres), str(res1rep), str(res1pm), res1name,
            str(usespm), str(pmspare), str(pmrefresh), str(pmmisdefer),
            str(pmfailreset), str(pmreqdefer), fnum(pmstagger), fnum(pmfreq),
            str(pmtriggered), pmtrig, str(pmdist), fnum(pp[0]), fnum(pp[1]), fnum(pp[2]),
            str(depnum), f"{depnothing:.2f}", f"{depidle:.2f}", f"{deppm:.2f}",
            f"{depfail:.2f}", f"{depthresh:.2f}", str(defidle), deptype]
    vals += [f"{c:.2f}" for c in costs]
    vals.append(str(adddone))
    cols = list(BLOCK_COLS)
    # disambiguated column keys (Param1#1 etc.) as rbd.py produces them
    row = {}
    seen: dict[str, int] = {}
    for c, v in zip(cols, vals):
        if c in seen:
            seen[c] += 1
            key = f"{c}#{seen[c]}"
        else:
            seen[c] = 1
            key = f"{c}#1" if cols.count(c) > 1 else c
        row[key] = v
    if phases:
        for i, pv in enumerate(phases, 1):
            row[f"P {i:03d}"] = pv
    return row


def default_block_template() -> dict:
    """The trailing ID=0 template row present in every Raptor 7 file.
    Note In/Out = 0 and GDLimit=1.0 in the real template."""
    return make_block_row(0, x=0.0, y=0.0, nlinks=(0, 0), fparams=(100.0, 0.0),
                    rparams=(10.0, 2.0), gdrate=1.0, gdlimit=1.0,
                    rso=1, stock=100, eso=1, costs=(1.0,) * 14)


def make_node_row(nid, name, x, y, parent=0, ntype=2, k=-1, n=1, out=1,
            cold=0, pr=0, autos=1, kstar=0, phases=0, depnum=0,
            deptype="None", divflow=1, fullflow=1, repcap=0, repna=1,
            phasevals=None):
    row = {"Name": name, "ID": str(nid), "xPos": fnum(x), "yPos": fnum(y),
           "parent": str(parent), "Type": str(ntype), "k": str(k), "n": str(n),
           "Out": str(out), "Cold": str(cold), "PR": str(pr), "Autos": str(autos),
           "k*": str(kstar), "Phases": str(phases), "depNum": str(depnum),
           "depType": deptype, "divFlow": str(divflow), "fullflow": str(fullflow),
           "repCap": str(repcap), "repNA": str(repna)}
    if phasevals:
        for i, pv in enumerate(phasevals, 1):
            row[f"P{i:03d}"] = pv
    return row


def make_event_row(eid, name, x, y, parent=0, prob=1.0, phasing=0,
             stream=SYS_STREAM_C, costs=(1.0, 1.0, 1.0), phasevals=None):
    row = {"eventName": name, "ID": str(eid), "xPos": fnum(x), "yPos": fnum(y),
           "ParentId": str(parent), "In": "1", "Out": "1", "Phasing": str(phasing),
           "Distro": str(D_BERNOULLI), "failStream": str(stream),
           "failVal": fnum(prob), "InitCost": f"{costs[0]:.2f}",
           "OpCost": f"{costs[1]:.2f}", "RepCost": f"{costs[2]:.2f}"}
    if phasevals:
        for i, pv in enumerate(phasevals, 1):
            row[f"P {i:03d}"] = pv
    return row


def make_link_row(lid, ftype, fid, ttype, tid, pid=0, coldpri=50, aprob=1.0,
            auto=0.0, man=0.0, cappri=50, nom=-2, maxf=-1):
    return {"ID": str(lid), "FromType": ftype, "FromID": str(fid),
            "ToType": ttype, "ToID": str(tid), "pID": str(pid),
            "ColdPri": str(coldpri), "AProb": fnum(aprob),
            "AutoTime": fnum(auto), "ManTime": fnum(man),
            "CapPri": str(cappri), "nomFlow": str(nom), "MaxFlow": str(maxf)}


def make_hierarchy_row(hid, name, x, y, parent=0, inid=0, outid=0, level=1, phs=0, zoom=14.0):
    return {"hierarchyName": name, "ID": str(hid), "xPos": fnum(x), "yPos": fnum(y),
            "Parent": str(parent), "In": "1", "Out": "1", "isCNd": "1",
            "zoom": f"{zoom:.2f}", "xCenter": "0.000000", "yCenter": "0.000000",
            "in": str(inid), "out": str(outid), "level": str(level),
            "Phs": str(phs), "xOrigin": "0.000000", "yOrigin": "80.000000",
            "myDepth": "0", "extraInt1": str(hid), "extraInt2": str(hid),
            "extraReal1": "0.000000", "extraReal2": "80.000000"}


def make_spare_pool_row(name, rso=0, stock=0, newsp=1, arrive=720.0, eso=0, emertime=24.0,
            slo=0, level=0, quan=1, slotime=24.0, spcost=1.0, emercost=1.0):
    return {"PoolName": name, "RSO": str(rso), "Stock": str(stock),
            "NewSp": str(newsp), "ArriveEvery": fnum(arrive), "ESO": str(eso),
            "EmerTime": fnum(emertime), "SLO": str(slo), "Level": str(level),
            "Quan": str(quan), "SLOTime": fnum(slotime), "SpCost": f"{spcost:.2f}",
            "emerShipCost": f"{emercost:.2f}"}


def make_resource_row(name, numin, sparecost=1.0, fixed=1.0, perunit=1.0, phases=0):
    return {"PoolName": name, "NumIn": str(numin), "SpareCost": f"{sparecost:.2f}",
            "FixedPerUse": f"{fixed:.2f}", "PerTimeUnit": f"{perunit:.2f}",
            "Phases": str(phases)}


def make_phase_row(name, num, mission=1, dist=D_NORM, params=(5.0, 0.1, 0.0)):
    p = list(params) + [0.0] * (3 - len(params))
    return {"PhaseName": name, "Num": str(num), "Mission": str(mission),
            "Dist": str(dist), "Param1": fnum(p[0]), "Param2": fnum(p[1]),
            "Param3": fnum(p[2])}


def make_trigger_row(name, initused=0.0, dist=D_NORM, params=(288.0, 1.0, 0.0), repeats=1):
    p = list(params) + [0.0] * (3 - len(params))
    return {"TriggerName": name, "IDnum": "0", "Repeats": str(repeats),
            "InitUsed": fnum(initused), "Dist": str(dist),
            "Param1": fnum(p[0]), "Param2": fnum(p[1]),
            "Param3": fnum(p[2])}


def fix_link_counts(blocks, nodes, events, links):
    """Recompute every element's In/Out (blocks) and n/Out (nodes) from the
    actual link list. The legacy writer stores these; wrong counts crash the
    Raptor 7 engine's path analysis (a hand-set Out smaller than the real
    outbound link count crashes Raptor7Student at Simulate)."""
    nin: dict[tuple[str, str], int] = {}
    nout: dict[tuple[str, str], int] = {}
    for link in links:
        from_end = (link["FromType"], link["FromID"])
        to = (link["ToType"], link["ToID"])
        nout[from_end] = nout.get(from_end, 0) + 1
        nin[to] = nin.get(to, 0) + 1
    for b in blocks:
        key = ("Block", b["ID"])
        b["In"] = str(nin.get(key, 0))
        b["Out"] = str(nout.get(key, 0))
    for e in events:
        key = ("Event", e["ID"])
        e["In"] = str(nin.get(key, 0))
        e["Out"] = str(nout.get(key, 0))
    for n in nodes:
        key = ("Node", n["ID"])
        n["Out"] = str(nout.get(key, 0))
        t = n["Type"]
        if t == "1":      # start marker: n always 0
            n["n"] = "0"
        elif t == "5":    # hierarchy-out marker: n = 1 by convention
            n["n"] = "1"
        else:
            # End (3), Regular (2) and In-marker (4) all take their real
            # incoming link count. An End node is NOT pinned to n=1: Raptor 7
            #accepts several paths converging on it directly
            # (golden fixtures MixedDepPerc/WakeOrderDependent both have
            # a Type-3 End with n=2 and two real incoming links) -- this
            # used to hard-code n=1 for End, which silently corrupted any
            # such model's End node back to n=1 the moment any other
            # topology edit ran `fix_link_counts`, turning a valid model
            # into one `builder.validate()` then (correctly) rejects.
            n["n"] = str(nin.get(key, 0))
    return blocks, nodes, events, links


def build_rbd(path, filename, sysdat, blocks=None, nodes=None, events=None,
              links=None, hiers=None, pools=None, resources=None, phases=None,
              triggers=None, comments=None, version="Version7_7.0.11 Student",
              hier_depth=0, nphases=0, timestamp="Sun Aug 16 12:00:00 2026"):
    """Assemble and write a complete .rbd file.

    Empirical continuation lines (blocks with D_EMPIRICAL) are appended
    manually after render_rbd output since render_rbd doesn't know them.
    """
    bl = blocks or []
    nd = nodes or []
    event = events or []
    link = links or []
    fix_link_counts(bl, nd, event, link)

    sections: dict[str, RbdSection] = {}
    sections["SYSTEM_DATA"] = RbdSection("SYSTEM_DATA", SYSTEM_COLS, [sysdat])
    hier_cols = list(HIER_COLS)
    sections["HIERARCHY_DATA"] = RbdSection("HIERARCHY_DATA", hier_cols, hiers or [])
    block_cols = list(BLOCK_COLS)
    # apply disambiguated names for duplicated Param columns
    from raptor.core.fileio.rbd import _disambiguate_columns
    block_cols = _disambiguate_columns(block_cols)
    if nphases:
        block_cols = block_cols + [f"P {i:03d}" for i in range(1, nphases + 1)]
    brows = bl + [default_block_template()]
    sections["BLOCK_DATA"] = RbdSection("BLOCK_DATA", block_cols, brows)
    event_cols = list(EVENT_COLS)
    if nphases:
        event_cols = event_cols + [f"P {i:03d}" for i in range(1, nphases + 1)]
    sections["EVENT_DATA"] = RbdSection("EVENT_DATA", event_cols, event)
    nd_cols = list(NODE_COLS)
    if nphases:
        nd_cols = nd_cols + [f"P{i:03d}" for i in range(1, nphases + 1)]
    sections["NODE_DATA"] = RbdSection("NODE_DATA", nd_cols, nd)
    sections["COMMENTS"] = RbdSection("COMMENTS", COMMENT_COLS, comments or [])
    sections["LINK_DATA"] = RbdSection("LINK_DATA", LINK_COLS, link)
    sections["SPARE_POOL_DATA"] = RbdSection("SPARE_POOL_DATA", SPARE_COLS, pools or [])
    sections["RESOURCE_DATA"] = RbdSection("RESOURCE_DATA", RES_COLS, resources or [])
    sections["PHASES"] = RbdSection("PHASES", PHASE_COLS, phases or [])
    sections["TRIGGERS"] = RbdSection("TRIGGERS", TRIGGER_COLS, triggers or [])

    if not hier_depth and hiers:
        hier_depth = max(int(h["level"]) for h in hiers)
    counts = {"Phases": nphases, "HierDepths": hier_depth}
    f = RbdFile(version=version, app_name="", file_name=filename,
                timestamp=timestamp, counts=counts, sections=sections)
    text = render_rbd(f)
    # legacy fidelity: strip trailing whitespace from every line (Raptor 7 writer
    # pads columns for alignment but never leaves ragged phase columns), then
    # write with CRLF line endings like every Raptor 7 fixture.
    lines = [ln.rstrip() for ln in text.split("\n")]
    # empirical continuation lines: a block with D_EMPIRICAL was written with
    # 0.0 params; the Raptor 7 writer appends an extra line right after that block
    # row holding all data points. render_rbd can't express this; insert here.
    if blocks:
        for _i, _br in enumerate(blocks):
            for _distkey, _pfx in (("FDist", "F"), ("RDist", "R")):
                pass  # handled by caller via emp_data mechanism below
    out = "\r\n".join(lines)
    if not out.endswith("\r\n"):
        out += "\r\n"
    with open(path, "w", encoding="latin-1", newline="") as fh:
        fh.write(out)
    return f


def insert_empirical(path, block_name, fail_points=None, repair_points=None):
    """Post-process a written .rbd: insert empirical continuation line(s)
    after the row of block `block_name` (legacy format: indented line holding
    all data points, per 's save routine)."""
    with open(path, encoding="latin-1", newline="") as fh:
        lines = fh.read().split("\r\n")
    out = []
    for ln in lines:
        out.append(ln)
        if ln.startswith(block_name + " "):
            if fail_points:
                out.append("                         " + "  ".join(
                    f"{p:16.6f}" for p in fail_points))
            if repair_points:
                out.append("                         " + "  ".join(
                    f"{p:16.6f}" for p in repair_points))
    with open(path, "w", encoding="latin-1", newline="") as fh:
        fh.write("\r\n".join(out))

"""Generate the .rbds model files for the RAPTOR Studio tutorial book.

Every file is a small teaching example written for this book. Regenerate
with:

    ../../.venv/bin/python gen_models.py     # from new_docs/tools

All files land in new_docs/models/ as .rbds (RAPTOR Studio JSON), so they
open both in RAPTOR Studio (this port's GUI) and the real Raptor 7 under
Wine.
"""
import os
import sys
sys.path.insert(0, '../..')
from raptor.core.fileio.rbd import parse_rbd
from raptor.core.fileio.rbds import render_rbds

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from raptor.core.fileio.rbdlib import (  # noqa: E402
    D_EXPO, D_FIXED, D_GAMMA, D_LOGNORM, D_NONE, D_NORM, D_WEIBULL,
    make_block_row, build_rbd, make_event_row, fix_link_counts, make_hierarchy_row, make_link_row, make_node_row,
    make_phase_row, make_spare_pool_row, make_system_row,
)

OUT = os.path.join(os.path.dirname(__file__), "..", "models")


def no_repair():
    return D_NONE, (1.0, 0.0, 0.0)


def series_model(fname, *, term, runs, units="hours", blocks):
    """blocks: list of (name, fdist, fparams, rdist, rparams).
    Returns after writing; layout: one row, start -> b1 -> ... -> bn -> end."""
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    x = 3.75
    for i, (nm, fd, fp, rd, rp) in enumerate(blocks, 1):
        blks.append(make_block_row(i, nm, x=x, y=91.25, fdist=fd, fparams=fp,
                             rdist=rd, rparams=rp))
        if i == 1:
            L("Node", 14, "Block", i)
        else:
            L("Block", i - 1, "Block", i)
        x += 2.5
    L("Block", len(blocks), "Node", 15)
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(15, "end", x + 1.25, 91.25, ntype=3),
    ]
    write(fname, term, runs, units, blks, nodes, links)


#: Node types that may have only one link out: Start, and a hierarchy's In.
_ONE_LINK_OUT = {"1", "4"}


def split_marker_fanout(blocks, nodes, events, hiers, links):
    """Raptor 7 allows one link out of a Start marker or a hierarchy's In
    marker ("Cannot have more than one connection from a Start Node!").
    Where a model fans out from one, put an ordinary `Split` node just
    after the marker and fan out from there, as that message says. The node
    copies the marker's flow settings, so capacity results do not move."""
    rows = blocks + nodes + events + (hiers or [])
    next_id = max(int(r["ID"]) for r in rows) + 1
    next_link = max(int(r["ID"]) for r in links) + 1
    for marker in list(nodes):
        if marker["Type"] not in _ONE_LINK_OUT:
            continue
        out = [lk for lk in links if lk["FromType"] == "Node" and lk["FromID"] == marker["ID"]]
        if len(out) < 2:
            continue
        split = make_node_row(next_id, "Split", float(marker["xPos"]) + 1.25, float(marker["yPos"]),
                        parent=int(marker["parent"]), divflow=int(marker["divFlow"]),
                        fullflow=int(marker["fullflow"]))
        nodes.append(split)
        for lk in out:
            lk["FromID"] = split["ID"]
        links.append(make_link_row(next_link, "Node", int(marker["ID"]), "Node", next_id,
                             pid=int(out[0]["pID"])))
        next_id += 1
        next_link += 1


def write(fname, term, runs, units, blocks, nodes, links, analtype="Availability",
          hiers=None, events=None, phases=None, pools=None, term_type="TimeSim",
          layout=False, **sys_kw):
    # The old writer defaults ESO=1, but that trips RAPTOR Studio's
    # preflight "emergency spare ordering" gate for blocks that never
    # use a spare pool; write 0 so tutorial models run out of the box.
    # A block that uses Pooled/Custom sparing keeps whatever ESO its own
    # make_block_row(...) call asked for (DC2's shared CRAC pool needs eso=1
    # for real emergency shipping).
    for b in blocks:
        if b.get("SpType") not in ("Pooled", "Custom"):
            b["ESO"] = "0"
    events = events or []
    split_marker_fanout(blocks, nodes, events, hiers, links)
    blocks, nodes, events, links = fix_link_counts(blocks, nodes, events, links)
    path = os.path.join(OUT, fname)
    tmp_legacy = path + ".tmpl"
    build_rbd(
        tmp_legacy, fname,
        make_system_row(term_type=term_type, term=float(term), runs=runs, units=units,
               analtype=analtype, **sys_kw),
        blocks=blocks, nodes=nodes, links=links, hiers=hiers, events=events,
        phases=phases, nphases=len(phases) if phases else 0, pools=pools,
    )
    # convert the generated legacy text to .rbds and drop the temp
    with open(tmp_legacy, encoding="latin-1") as f:
        rbd = parse_rbd(f.read())
    # A node with several inputs must declare how many it needs: Raptor 7
    # opens the unset k=-1 as "required number never declared" (the same
    # 1-of-n our engine gives it, so results do not move).
    from raptor.core.fileio.legacy_layout import normalize_node_kofn
    normalize_node_kofn(rbd)
    if layout:
        # Place every level on Auto Layout's grid rather than the hand
        # coordinates above (the data centre's levels were packed so
        # tightly that its home level drew as a hairline).
        from raptor.core.fileio.legacy_layout import normalize_legacy_layout
        normalize_legacy_layout(rbd, force=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_rbds(rbd, meta={"file_name": fname}))
    os.remove(tmp_legacy)
    print("wrote", path)


# ---------------------------------------------------------------- Lantern1
# Camping lantern: Battery_Pack -> Switch -> LED_Panel, series, no repair.
series_model(
    "Lantern1.rbds", term=48, runs=10,
    blocks=[
        ("Battery_Pack", D_WEIBULL, (1.5, 60.0), *no_repair()),
        ("Switch", D_EXPO, (200.0,), *no_repair()),
        ("LED_Panel", D_NORM, (100.0, 10.0), *no_repair()),
    ],
)

# --------------------------------------------------------------- Espresso1
# Coffee cart: Pump_A and Pump_B in parallel (either one keeps pressure),
# then Boiler in series. No repair.
def espresso():
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    def pump(bid, nm, x, y):
        return make_block_row(bid, nm, x=x, y=y, fdist=D_EXPO,
                        fparams=(400.0,), rdist=D_NONE, rparams=(1.0,))
    blks.append(pump(1, "Pump_A", 6.25, 92.50))
    blks.append(pump(2, "Pump_B", 6.25, 90.00))
    blks.append(make_block_row(3, "Boiler", 11.25, 91.25,
                         fdist=D_WEIBULL, fparams=(2.0, 900.0),
                         rdist=D_NONE, rparams=(1.0,)))
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(16, "split", 3.75, 91.25, ntype=2, k=-1, n=1),
        make_node_row(17, "Either_pump", 8.75, 91.25, ntype=2, k=1, n=2),
        make_node_row(15, "end", 13.75, 91.25, ntype=3),
    ]
    L("Node", 14, "Node", 16)
    L("Node", 16, "Block", 1)
    L("Node", 16, "Block", 2)
    L("Block", 1, "Node", 17)
    L("Block", 2, "Node", 17)
    L("Node", 17, "Block", 3)
    L("Block", 3, "Node", 15)
    write("Espresso1.rbds", 500, 10, "hours", blks, nodes, links)


espresso()

# ------------------------------------------------------------------ Drone1
# Delivery hexacopter: Battery in series with a 4-of-6 motor vote.
def drone():
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    # Laid out on Auto Layout's grid (2.5-unit rows, 5-unit columns: the
    # split node, the six motors fanned out, the vote node), with Motor_1
    # at the top. The old hand placement had the split node AFTER the
    # motors, so every link crossed the diagram.
    blks.append(make_block_row(1, "Battery", 3.75, 92.5,
                         fdist=D_NORM, fparams=(60.0, 6.0),
                         rdist=D_NONE, rparams=(1.0,)))
    ys = [98.75 - 2.5 * i for i in range(6)]
    for i, y in enumerate(ys, 1):
        blks.append(make_block_row(1 + i, "Motor_%d" % i, 8.75, y,
                             fdist=D_EXPO, fparams=(120.0,),
                             rdist=D_NONE, rparams=(1.0,)))
    blks.append(make_block_row(8, "Flight_Controller", 13.75, 92.5,
                         fdist=D_WEIBULL, fparams=(1.2, 300.0),
                         rdist=D_NONE, rparams=(1.0,)))
    nodes = [
        make_node_row(14, "start", 1.25, 92.5, ntype=1),
        make_node_row(16, "Motors_need_4", 11.25, 92.5, ntype=2, k=4, n=6),
        make_node_row(17, "join", 6.25, 92.5, ntype=2, k=-1, n=7),
        make_node_row(15, "end", 16.25, 92.5, ntype=3),
    ]
    L("Node", 14, "Block", 1)
    L("Block", 1, "Node", 17)
    for m in range(2, 8):
        L("Node", 17, "Block", m)
        L("Block", m, "Node", 16)
    L("Node", 16, "Block", 8)
    L("Block", 8, "Node", 15)
    write("Drone1.rbds", 48, 10, "hours", blks, nodes, links)


drone()

# ------------------------------------------- Toaster_Fixed/Expo/Weibull
# One heating element, same ~1000 h average life, three spread shapes.
# Warranty question: does it die inside the first 600 h?
for tag, fd, fp in [
    ("Fixed", D_FIXED, (1000.0,)),
    ("Expo", D_EXPO, (1000.0,)),
    ("Weibull", D_WEIBULL, (1.5, 1100.0)),
]:
    series_model(
        "Toaster_%s.rbds" % tag, term=600, runs=40,
        blocks=[("Heating_Element", fd, fp, *no_repair())],
    )

# ------------------------------------------------------------------ Vendo1
# Soda vending machine with repairs: Compressor fails often but is quick to
# fix; Keypad fails rarely but the part ships slow (fixed 12 h repair).
series_model(
    "Vendo1.rbds", term=8760, runs=20,
    blocks=[
        ("Compressor", D_EXPO, (400.0,), D_LOGNORM, (4.0, 1.5)),
        ("Keypad", D_EXPO, (600.0,), D_FIXED, (12.0,)),
    ],
)

print("done")

# --------------------------------------------------------------- TacoTruck1
# Ch7 custom sparing: Propane_Tank has 3 spares on the shelf; each tank
# failure consumes one; out of tanks = truck closes for good.
def tacotruck():
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    blks.append(make_block_row(1, "Propane_Tank", x=5.0, y=91.25,
                         fdist=D_EXPO, fparams=(200.0,),
                         rdist=D_FIXED, rparams=(2.0,),
                         sptype="Custom", rso=0, stock=3, newsp=1,
                         eso=0))  # ch07's own story: no emergency
                         # shipping -- out of tanks means closed for
                         # good. Explicit since write() only forces
                         # ESO=0 for Infinite-sparing blocks.
    blks.append(make_block_row(2, "Grill", x=8.75, y=91.25,
                         fdist=D_WEIBULL, fparams=(1.5, 900.0),
                         rdist=D_LOGNORM, rparams=(4.0, 1.0)))
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(15, "end", 12.5, 91.25, ntype=3),
    ]
    L("Node", 14, "Block", 1)
    L("Block", 1, "Block", 2)
    L("Block", 2, "Node", 15)
    write("TacoTruck1.rbds", 1000, 20, "hours", blks, nodes, links)


tacotruck()

# ----------------------------------------------------------------- Bakery1
# Ch8 cold standby: Main_Oven runs, Standby_Oven waits cold; the Cold node
# switches between them (link ColdPri: lower number = higher priority).
def bakery():
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti, cpri):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti, coldpri=cpri,
                             aprob=1.0, auto=0.5))

    oven = lambda bid, nm, y: make_block_row(bid, nm, x=3.75, y=y,
                                       fdist=D_EXPO, fparams=(300.0,),
                                       rdist=D_LOGNORM, rparams=(6.0, 1.0))
    # Auto Layout's grid, Main_Oven on top.
    blks.append(oven(1, "Main_Oven", 93.75))
    blks.append(oven(2, "Standby_Oven", 91.25))
    blks.append(make_block_row(3, "Power", x=8.75, y=92.5,
                         fdist=D_WEIBULL, fparams=(1.3, 1500.0),
                         rdist=D_FIXED, rparams=(4.0,)))
    nodes = [
        make_node_row(14, "start", 1.25, 92.5, ntype=1),
        # Cold standby node: k=1 of n=2, Cold=1, k*=1 (one slot active)
        make_node_row(16, "Ovens_cold_swap", x=6.25, y=92.5, ntype=2,
                k=1, n=2, out=1, cold=1, pr=1, autos=1, kstar=1),
        make_node_row(15, "end", 11.25, 92.5, ntype=3),
    ]
    L("Node", 14, "Block", 1, 50)
    L("Node", 14, "Block", 2, 50)
    L("Block", 1, "Node", 16, 25)   # main oven: active slot
    L("Block", 2, "Node", 16, 50)   # standby oven: colder priority
    L("Node", 16, "Block", 3, 50)
    L("Block", 3, "Node", 15, 50)
    write("Bakery1.rbds", 720, 20, "hours", blks, nodes, links)


bakery()

# ---------------------------------------------------------------- CarCare
# Ch9 preventive maintenance: Moped_Engine wears out (Weibull shape 2);
# an oil service every PMFreq hours refreshes it like new. Two variants:
# without (CarCare0) and with (CarCare1) PM.
def carcare(fname, uses_pm):
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    blks.append(make_block_row(1, "Moped_Engine", x=6.25, y=91.25,
                         fdist=D_WEIBULL, fparams=(2.0, 800.0),
                         rdist=D_LOGNORM, rparams=(8.0, 2.0),
                         usespm=1 if uses_pm else 0,
                         pmspare=0, pmrefresh=1, pmfailreset=1,
                         pmreqdefer=0, pmfreq=400.0,
                         pmdist=D_FIXED, pmparams=(2.0,)))
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(15, "end", 11.25, 91.25, ntype=3),
    ]
    L("Node", 14, "Block", 1)
    L("Block", 1, "Node", 15)
    write(fname, 2000, 20, "hours", blks, nodes, links)


carcare("CarCare0.rbds", False)
carcare("CarCare1.rbds", True)

# ---------------------------------------------------------------- Van1/VanFlat
# Ch10 hierarchies. Same physics both files:
#   Fuel_Pump Expo(500) fail / Lognorm(4,0.5) repair
#   Engine_Core Weibull(2,1000) fail / Lognorm(6,1) repair
#   Tire_x4    Expo(800) fail / Fixed(2) repair, van needs 3 of 4
# Van1 wraps pump+engine in Engine_sub and tires in Tires_sub hierarchies;
# VanFlat is the identical flat diagram.
def van_parts():
    blks = [
        make_block_row(3, "Fuel_Pump", x=4.0, y=93.75,
                 fdist=D_EXPO, fparams=(500.0,),
                 rdist=D_LOGNORM, rparams=(4.0, 0.5)),
        make_block_row(4, "Engine_Core", x=7.5, y=93.75,
                 fdist=D_WEIBULL, fparams=(2.0, 1000.0),
                 rdist=D_LOGNORM, rparams=(6.0, 1.0)),
        make_block_row(5, "Tire_FL", x=4.0, y=88.75,
                 fdist=D_EXPO, fparams=(800.0,),
                 rdist=D_FIXED, rparams=(2.0,)),
        make_block_row(6, "Tire_FR", x=6.5, y=88.75,
                 fdist=D_EXPO, fparams=(800.0,),
                 rdist=D_FIXED, rparams=(2.0,)),
        make_block_row(7, "Tire_RL", x=9.0, y=88.75,
                 fdist=D_EXPO, fparams=(800.0,),
                 rdist=D_FIXED, rparams=(2.0,)),
        make_block_row(8, "Tire_RR", x=11.5, y=88.75,
                 fdist=D_EXPO, fparams=(800.0,),
                 rdist=D_FIXED, rparams=(2.0,)),
    ]
    return blks


def van_hier():
    blks = van_parts()
    for b in blks[:2]:
        b["ParentId"] = "10"   # Fuel_Pump, Engine_Core -> Engine_sub
    for b in blks[2:]:
        b["ParentId"] = "20"   # tires -> Tires_sub
        b["yPos"] = f"{87.5 + 1.25 * blks[2:].index(b):.6f}"
    links = [
        make_link_row(1, "Node", 1, "Hier", 10),
        make_link_row(2, "Hier", 10, "Node", 30),
        make_link_row(3, "Node", 30, "Hier", 20),
        make_link_row(4, "Hier", 20, "Node", 2),
        # inside Engine_sub
        make_link_row(5, "Node", 11, "Block", 3, pid=10),
        make_link_row(6, "Block", 3, "Block", 4, pid=10),
        make_link_row(7, "Block", 4, "Node", 12, pid=10),
        # inside Tires_sub
        make_link_row(8, "Node", 21, "Block", 5, pid=20),
        make_link_row(9, "Node", 21, "Block", 6, pid=20),
        make_link_row(10, "Node", 21, "Block", 7, pid=20),
        make_link_row(11, "Node", 21, "Block", 8, pid=20),
        make_link_row(12, "Block", 5, "Node", 13, pid=20),
        make_link_row(13, "Block", 6, "Node", 13, pid=20),
        make_link_row(14, "Block", 7, "Node", 13, pid=20),
        make_link_row(15, "Block", 8, "Node", 13, pid=20),
        make_link_row(16, "Node", 13, "Node", 22, pid=20),
    ]
    nodes = [
        make_node_row(1, "start", 1.25, 91.25, ntype=1),
        make_node_row(2, "end", 16.25, 91.25, ntype=3),
        make_node_row(30, "mid", 9.75, 91.25, ntype=2, k=-1, n=1),
        make_node_row(11, "in", 1.25, 93.75, parent=10, ntype=4, k=1, n=0),
        make_node_row(12, "out", 12.5, 93.75, parent=10, ntype=5),
        make_node_row(21, "in", 1.25, 88.75, parent=20, ntype=4, k=1, n=0),
        make_node_row(13, "Tires_vote", 15.0, 88.75, parent=20,
                ntype=2, k=3, n=4),
        make_node_row(22, "out", 17.5, 88.75, parent=20, ntype=5),
    ]
    hiers = [
        make_hierarchy_row(10, "Engine_sub", 6.25, 91.25, inid=11, outid=12),
        make_hierarchy_row(20, "Tires_sub", 13.25, 91.25, inid=21, outid=22),
    ]
    return blks, nodes, links, hiers


def van_flat():
    blks = van_parts()
    links = [
        make_link_row(1, "Node", 1, "Block", 3),
        make_link_row(2, "Block", 3, "Block", 4),
        make_link_row(3, "Block", 4, "Node", 30),
        make_link_row(4, "Node", 30, "Block", 5),
        make_link_row(5, "Node", 30, "Block", 6),
        make_link_row(6, "Node", 30, "Block", 7),
        make_link_row(7, "Node", 30, "Block", 8),
        make_link_row(8, "Block", 5, "Node", 13),
        make_link_row(9, "Block", 6, "Node", 13),
        make_link_row(10, "Block", 7, "Node", 13),
        make_link_row(11, "Block", 8, "Node", 13),
        make_link_row(12, "Node", 13, "Node", 2),
    ]
    nodes = [
        make_node_row(1, "start", 1.25, 91.25, ntype=1),
        make_node_row(2, "end", 16.25, 91.25, ntype=3),
        make_node_row(30, "mid", 8.75, 91.25, ntype=2, k=-1, n=1),
        make_node_row(13, "Tires_vote", 13.75, 91.25, ntype=2, k=3, n=4),
    ]
    for b, y in zip(blks, [91.25] * 2 + [86.25] * 4):
        b["yPos"] = f"{y:.6f}"
    blks[0]["xPos"], blks[1]["xPos"] = "3.750000", "6.250000"
    for i, b in enumerate(blks[2:]):
        b["xPos"] = f"{3.75 + 2.5 * i:.6f}"
    return blks, nodes, links


blks, nodes, links, hiers = van_hier()
write("Van1.rbds", 2000, 20, "hours", blks, nodes, links, hiers=hiers)

blks, nodes, links = van_flat()
write("VanFlat.rbds", 2000, 20, "hours", blks, nodes, links)

# ------------------------------------------------------------------ Water1
# Town water utility: three pumps in parallel, each pipe has a capacity
# (nominal / max). Demand = 180 units/h — more than any two pipes can
# carry, so every pump outage loses flow. Loss when pump X is down =
# 180 minus the max of the two surviving pipes.
def water():
    blks, nodes, links = [], [], []
    lid = 0

    def L(ft, fi, tt, ti, **kw):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti, **kw))

    names = ["Pump_A", "Pump_B", "Pump_C"]
    for i, nm in enumerate(names):
        blks.append(make_block_row(i + 1, nm, x=10.0, y=87.5 + 3.75 * i,
                             fdist=D_EXPO, fparams=(500.0,),
                             rdist=D_FIXED, rparams=(24.0,)))

    nodes.append(make_node_row(10, "start", 6.25, 91.25, ntype=1))
    nodes.append(make_node_row(11, "Manifold", 14.75, 91.25, ntype=2,
                         k=-1, n=1, out=1, divflow=0, fullflow=0))
    nodes.append(make_node_row(13, "end", 18.25, 91.25, ntype=3))

    caps = [dict(nom=30, maxf=50), dict(nom=30, maxf=60), dict(nom=50, maxf=80)]
    for b, c in zip((1, 2, 3), caps):
        L("Node", 10, "Block", b)
        L("Block", b, "Node", 11, cappri=50 + 10 * (b - 1), **c)
    L("Node", 11, "Node", 13)
    return blks, nodes, links


blks, nodes, links = water()
write("Water1.rbds", 8760, 20, "hours", blks, nodes, links,
      flow=180, cap=1, lostcost=0.0)

# ------------------------------------------------------------------ Water2
# Water1 plus a bill: purchase cost per pump, repair crew costs, cost per
# hour of full outage (red), and a price per lost megalitre. Costs tuple:
# (initial, Running, standby, idle, repHold, repPerTime, repFixed,
#  donePerTime, doneFixed, pmHold, pm, pmFixed, perSpare, emerShipping).
def water2():
    blks, nodes, links = [], [], []
    lid = 0

    def L(ft, fi, tt, ti, **kw):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti, **kw))

    names = ["Pump_A", "Pump_B", "Pump_C"]
    for i, nm in enumerate(names):
        blks.append(make_block_row(i + 1, nm, x=10.0, y=87.5 + 3.75 * i,
                             fdist=D_EXPO, fparams=(500.0,),
                             rdist=D_FIXED, rparams=(24.0,),
                             costs=(40000.0, 0.0, 0.0, 0.0, 0.0,
                                    100.0, 2000.0, 0.0, 0.0, 0.0,
                                    0.0, 0.0, 0.0, 0.0)))

    nodes.append(make_node_row(10, "start", 6.25, 91.25, ntype=1))
    nodes.append(make_node_row(11, "Manifold", 14.75, 91.25, ntype=2,
                         k=-1, n=1, out=1, divflow=0, fullflow=0))
    nodes.append(make_node_row(13, "end", 18.25, 91.25, ntype=3))

    caps = [dict(nom=30, maxf=50), dict(nom=30, maxf=60), dict(nom=50, maxf=80)]
    for b, c in zip((1, 2, 3), caps):
        L("Node", 10, "Block", b)
        L("Block", b, "Node", 11, cappri=50 + 10 * (b - 1), **c)
    L("Node", 11, "Node", 13)
    return blks, nodes, links


blks, nodes, links = water2()
write("Water2.rbds", 8760, 20, "hours", blks, nodes, links,
      flow=180, cap=1, cost=1, redcost=5000.0, lostcost=40.0)

# ------------------------------------------------------------------ DC1/DC2
# Data-centre capstone: facility infrastructure as four hierarchies in
# series. Utility_sub = grid feed(s). Power_sub = switchgear (PM'd in DC2)
# with a cold-standby generator behind the ATS (wake gap = AutoTime) and a
# UPS. Cooling_sub = N+1 CRACs (2-of-3 vote, spare CRAC in a stock pool)
# feeding a chilled-water pump. IT_sub = dual PDUs (either carries) into
# two server rows (both needed). Downtime billed at 300k/h. Target:
# Tier III-style availability, 99.982% (<= 1.6 h down per year).
NO_COST = (0.0,) * 14


def data_centre(fname, ats, pm_switchgear):
    blks, nodes, links, hiers = [], [], [], []
    lid = 0

    def L(*args, **kw):
        nonlocal lid
        lid += 1
        row = make_link_row(lid, *args, **kw)
        links.append(row)
        return row

    # --- Power_sub (hid=20): utility feed(s) + cold-standby generator
    # behind the ATS (wake gap = AutoTime), UPS, then switchgear.
    # DC2 adds a second utility feed (2N) and dual PM'd switchgear
    # strings (weekly PM, staggered — one string carries while the
    # other is serviced; the Tier III idea). Everything power-related
    # lives inside the hierarchy so a dead ATS kills the oval and the
    # outage propagates to the system. ------------------------------------
    sw_pm = dict(usespm=1, pmspare=0, pmrefresh=1, pmfailreset=1,
                 pmreqdefer=0, pmfreq=168.0, pmdist=D_FIXED,
                 pmparams=(2.0,))
    blks.append(make_block_row(101, "Utility_A", x=3.5, y=87.0, parent=20,
                         fdist=D_EXPO, fparams=(1000.0,),
                         rdist=D_FIXED, rparams=(8.0,), costs=NO_COST))
    L("Node", 21, "Block", 101, pid=20, coldpri=50, auto=ats)
    L("Block", 101, "Node", 22, pid=20, coldpri=25, auto=ats)
    blks.append(make_block_row(103, "Generator", x=6.0, y=97.5, parent=20,
                         fdist=D_EXPO, fparams=(600.0,),
                         rdist=D_LOGNORM, rparams=(6.0, 1.0), costs=NO_COST))
    L("Node", 21, "Block", 103, pid=20, coldpri=50, auto=ats)
    L("Block", 103, "Node", 22, pid=20, coldpri=50, auto=ats)
    blks.append(make_block_row(104, "UPS", x=10.5, y=91.25, parent=20,
                         fdist=D_EXPO, fparams=(80000.0,),
                         rdist=D_FIXED, rparams=(2.0,) if pm_switchgear else (4.0,), costs=NO_COST))
    L("Node", 22, "Block", 104, pid=20, coldpri=25, auto=ats)
    if pm_switchgear:
        L("Block", 104, "Node", 26, pid=20)
        blks += [
            make_block_row(10, "Switchgear_A", x=11.25, y=88.75, parent=20,
                     fdist=D_WEIBULL, fparams=(2.0, 30000.0),
                     rdist=D_FIXED, rparams=(6.0,), costs=NO_COST, **sw_pm),
            make_block_row(12, "Switchgear_B", x=11.25, y=93.75, parent=20,
                     fdist=D_WEIBULL, fparams=(2.0, 30000.0),
                     rdist=D_FIXED, rparams=(6.0,), costs=NO_COST),
        ]
        for a in (10, 12):
            L("Node", 26, "Block", a, pid=20)
            L("Block", a, "Node", 25, pid=20)
        L("Node", 25, "Node", 23, pid=20)
    else:
        blks.append(make_block_row(110, "Switchgear", x=13.0, y=91.25, parent=20,
                             fdist=D_EXPO, fparams=(30000.0,),
                             rdist=D_FIXED, rparams=(6.0,), costs=NO_COST))
        L("Block", 104, "Block", 110, pid=20)
        L("Block", 110, "Node", 23, pid=20)
    power_nodes = [
        make_node_row(21, "in", 1.25, 91.25, parent=20, ntype=4, k=1, n=0),
        make_node_row(22, "ATS_swap", 7.75, 91.25, parent=20, ntype=2,
                k=1, n=2, out=1, cold=1, pr=1, autos=1, kstar=1),
        make_node_row(23, "out", 15.5, 91.25, parent=20, ntype=5),
    ]
    if pm_switchgear:
        power_nodes.append(make_node_row(25, "String_par", 13.0, 91.25, parent=20,
                                   ntype=2, k=1, n=2))
        power_nodes.append(make_node_row(26, "Str_mid", 10.0, 91.25, parent=20,
                                   ntype=2, k=-1, n=1))
    nodes += power_nodes
    L("Node", 1, "Hier", 20)
    L("Hier", 20, "Node", 5)
    L("Node", 5, "Hier", 30)

    # --- Cooling_sub (hid=30): 2-of-3 CRACs, sharing ONE spare pool ----
    # `CRAC_Spares`: 3 units on the shelf, monthly resupply (30 days),
    # emergency shipping in 48h if the shelf ever runs dry. With these
    # numbers the pool essentially never empties across a real 8760h
    # year -- the story is "a well-stocked shelf", matching a tuned
    # Tier III facility. ESO=1 here is deliberate (DC's `write()` call
    # only zeroes ESO for Infinite-sparing blocks, see that function).
    for i, nm in enumerate(("CRAC_A", "CRAC_B", "CRAC_C")):
        blks.append(make_block_row(20 + i, nm, x=5.0 + 2.5 * i, y=87.5 + 1.25 * i,
                             parent=30, fdist=D_EXPO, fparams=(2500.0,),
                             rdist=D_FIXED, rparams=(2.0,), costs=NO_COST,
                             pool="CRAC_Spares", sptype="Pooled",
                             rso=1, stock=100, eso=1))
        L("Node", 31, "Block", 20 + i, pid=30)
        L("Block", 20 + i, "Node", 32, pid=30)
    blks.append(make_block_row(23, "Chill_Pump", x=12.5, y=91.25, parent=30,
                         fdist=D_EXPO, fparams=(80000.0,),
                         rdist=D_FIXED, rparams=(2.0,) if pm_switchgear else (4.0,), costs=NO_COST))
    nodes += [
        make_node_row(31, "in", 1.25, 91.25, parent=30, ntype=4, k=1, n=0),
        make_node_row(32, "Cool_vote", 10.0, 91.25, parent=30, ntype=2, k=2, n=3),
        make_node_row(33, "out", 16.25, 91.25, parent=30, ntype=5),
    ]
    L("Node", 32, "Block", 23, pid=30)
    L("Block", 23, "Node", 33, pid=30)
    L("Hier", 30, "Node", 6)

    # --- IT_sub (hid=40): dual PDUs -> two server rows (both needed) ---
    for i, nm in enumerate(("PDU_A", "PDU_B")):
        blks.append(make_block_row(30 + i, nm, x=5.0, y=90.0 + 3.75 * i, parent=40,
                             fdist=D_EXPO, fparams=(50000.0,),
                             rdist=D_FIXED, rparams=(4.0,), costs=NO_COST))
        L("Node", 41, "Block", 30 + i, pid=40)
        L("Block", 30 + i, "Node", 43, pid=40)
    for i, nm in enumerate(("Server_Row_1", "Server_Row_2", "Server_Row_3")):
        blks.append(make_block_row(32 + i, nm, x=10.0, y=88.75 + 2.5 * i, parent=40,
                             fdist=D_EXPO, fparams=(1500.0,),
                             rdist=D_FIXED, rparams=(3.0,), costs=NO_COST))
        L("Node", 43, "Block", 32 + i, pid=40)
        L("Block", 32 + i, "Node", 42, pid=40)
    L("Node", 42, "Node", 44, pid=40)
    nodes += [
        make_node_row(41, "in", 1.25, 91.25, parent=40, ntype=4, k=1, n=0),
        make_node_row(43, "PDU_par", 7.5, 91.25, parent=40, ntype=2, k=1, n=2),
        make_node_row(42, "Row_vote", 12.5, 91.25, parent=40, ntype=2, k=2, n=3),
        make_node_row(44, "out", 16.25, 91.25, parent=40, ntype=5),
    ]
    L("Node", 6, "Hier", 40)
    L("Hier", 40, "Node", 2)

    # --- top level nodes -------------------------------------------------
    nodes += [
        make_node_row(1, "start", 1.25, 91.25, ntype=1),
        make_node_row(2, "end", 21.25, 91.25, ntype=3),
        make_node_row(5, "mid1", 11.75, 91.25, ntype=2, k=-1, n=1),
        make_node_row(6, "mid2", 17.25, 91.25, ntype=2, k=-1, n=1),
    ]
    hiers = [
        make_hierarchy_row(20, "Power_sub", 6.25, 91.25, inid=21, outid=23),
        make_hierarchy_row(30, "Cooling_sub", 14.25, 91.25, inid=31, outid=33),
        make_hierarchy_row(40, "IT_sub", 19.25, 91.25, inid=41, outid=44),
    ]
    pools = [make_spare_pool_row("CRAC_Spares", rso=1, stock=3, newsp=1, arrive=720.0,
                     eso=1, emertime=48.0)]
    write(fname, 8760, 20, "hours", blks, nodes, links,
          hiers=hiers, cost=1, redcost=300000.0, pools=pools, layout=True)


data_centre("DC1.rbds", ats=0.1, pm_switchgear=False)
data_centre("DC2.rbds", ats=0.05, pm_switchgear=True)

# --------------------------------------------------------------- EspressoDep1
# Ch. Dependency: same Espresso1 diagram (Pump_A + Pump_B feeding
# Either_pump, "need 1 of 2", then Boiler) -- except Pump_B's activation
# relay is wired off Pump_A's own control board (a wiring shortcut),
# so Pump_B is elementally dependent on Pump_A (depnum=Pump_A's id,
# deptype=RBDBlock, depidle=100.0): whenever Pump_A is down, Pump_B is
# forced Idle too, no matter its own health. Since Pump_A alone already
# satisfies the node's "need 1 of 2" rule whenever IT is up, Pump_B's
# state never matters -- the "redundant" pair behaves exactly like
# Pump_A alone. Reliability collapses from Espresso1's own ~0.37
# (independent pumps) to ~0.21 (Espresso1's own single-pump figure),
# even though the diagram still shows two parallel pumps.
def espresso_dep():
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    blks.append(make_block_row(1, "Pump_A", x=6.25, y=92.50, fdist=D_EXPO,
                         fparams=(400.0,), rdist=D_NONE, rparams=(1.0,)))
    blks.append(make_block_row(2, "Pump_B", x=6.25, y=90.00, fdist=D_EXPO,
                         fparams=(400.0,), rdist=D_NONE, rparams=(1.0,),
                         depnum=1, deptype="RBDBlock", depidle=100.0))
    blks.append(make_block_row(3, "Boiler", x=11.25, y=91.25,
                         fdist=D_WEIBULL, fparams=(2.0, 900.0),
                         rdist=D_NONE, rparams=(1.0,)))
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(16, "split", 3.75, 91.25, ntype=2, k=-1, n=1),
        make_node_row(17, "Either_pump", 8.75, 91.25, ntype=2, k=1, n=2),
        make_node_row(15, "end", 13.75, 91.25, ntype=3),
    ]
    L("Node", 14, "Node", 16)
    L("Node", 16, "Block", 1)
    L("Node", 16, "Block", 2)
    L("Block", 1, "Node", 17)
    L("Block", 2, "Node", 17)
    L("Node", 17, "Block", 3)
    L("Block", 3, "Node", 15)
    write("EspressoDep1.rbds", 500, 10, "hours", blks, nodes, links)


espresso_dep()

# ------------------------------------------------------------- FinaleShow1
# Ch. Events: a fireworks finale fires five shells at once from one
# control board. Each shell either fires or is a dud -- a one-shot
# decision made the instant the button is pressed, not something with
# an operating clock. Modeled as five Bernoulli EVENTS (not blocks),
# each with an 80% chance of firing (Distro=17/Bernoulli, failVal=0.8
# -- draw==1.0 is "success" per events.py's conduct_event), feeding a
# "need 3 of 5" vote node. No blocks in this model at all -- same shape
# as `tests/golden/Events`. Analytic check: n=5, p=0.8,
# P(>=3) = C(5,3)p^3q^2 + C(5,4)p^4q + p^5
# = 0.2048 + 0.4096 + 0.32768 = 0.94208.
def finale_show():
    evs = [make_event_row(i, f"Shell_{i}", x=8.75, y=95.0 - 1.25 * i, prob=0.8)
           for i in range(1, 6)]
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(16, "Finale_vote", 13.75, 91.25, ntype=2, k=3, n=5),
        make_node_row(15, "end", 18.75, 91.25, ntype=3),
    ]
    links = [make_link_row(i, "Node", 14, "Event", i) for i in range(1, 6)]
    links += [make_link_row(5 + i, "Event", i, "Node", 16) for i in range(1, 6)]
    links.append(make_link_row(11, "Node", 16, "Node", 15))
    write("FinaleShow1.rbds", 1, 1000, "hours", [], nodes, links, events=evs)


finale_show()

# ------------------------------------------------------------ CarWash1
# Ch. Phasing: an automatic car wash cycles every car through three
# stations -- Soap, Rinse, Dry -- and repeats for the next car, all
# shift long. The dryer blower is switched out of the circuit during
# Soap (no point running it into suds) and back in for Rinse and Dry:
# phase_type "C" (Cut) then "A" (Active) then "A", the pattern
# legacy/rbd/Phasing1.md documents for the real Raptor SolarP_500W model
# and tests/golden/Phasing1 captures: a phase-1 Cut block cascades its
# own node/system status to Bad/Red immediately at kickoff (orchestrator.py
# SetToInitValues port). Soap is also flagged Mission=0: the wash's real
# service commitment is Rinse+Dry, so a blower "outage" that is really
# just the scheduled Soap window should not count against Dependability
# the way it counts against plain Availability -- this chapter's whole
# point.
def car_wash():
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    blks.append(make_block_row(1, "Track_Motor", x=6.25, y=91.25,
                         fdist=D_EXPO, fparams=(600.0,),
                         rdist=D_FIXED, rparams=(5.0,)))
    blks.append(make_block_row(2, "Blower", x=11.25, y=91.25,
                         fdist=D_EXPO, fparams=(240.0,),
                         rdist=D_FIXED, rparams=(5.0,),
                         phasing=1,
                         phases=["C:   0.000", "A:   1.000", "A:   1.000"]))
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(15, "end", 16.25, 91.25, ntype=3),
    ]
    L("Node", 14, "Block", 1)
    L("Block", 1, "Block", 2)
    L("Block", 2, "Node", 15)
    phases = [
        make_phase_row("Soap", 1, mission=0, dist=D_NORM, params=(2.0, 0.1)),
        make_phase_row("Rinse", 2, mission=1, dist=D_NORM, params=(2.0, 0.1)),
        make_phase_row("Dry", 3, mission=1, dist=D_NORM, params=(3.0, 0.15)),
    ]
    write("CarWash1.rbds", 480, 20, "minutes", blks, nodes, links,
          phases=phases)


car_wash()

# ------------------------------------------------------------ Laundromat1
# Ch. Transients: one washer, Weibull wear-out (shape 2.5, scale 200h),
# Lognormal repair (mean 12h, sd 4h). Shipped preset to a naive 200-hour
# mission, Statistics start time = 0. The chapter has the reader widen
# the mission to 2200h and set Statistics start time to 2000h (System
# Settings) to see the "steady-state" number underneath the new-machine
# honeymoon: naive 200h run ~0.96 availability, 2000h-delayed
# steady-state ~0.94. StartStats is honoured for every model by
# orchestrator.py's `_delayed_stats_start`.
def laundromat():
    blk = make_block_row(1, "Washer", x=6.25, y=91.25, fdist=D_WEIBULL,
                   fparams=(2.5, 200.0), rdist=D_LOGNORM, rparams=(12.0, 4.0))
    nodes = [make_node_row(14, "start", 1.25, 91.25, ntype=1),
             make_node_row(15, "end", 11.25, 91.25, ntype=3)]
    links = [make_link_row(1, "Node", 14, "Block", 1),
             make_link_row(2, "Block", 1, "Node", 15)]
    write("Laundromat1.rbds", 200, 200, "hours", [blk], nodes, links,
          start_stats=0.0)


laundromat()

# -------------------------------------------------------------- ProdLine0/1
# Capstone I: a bottling line, 14 stations, pure series (one broken
# link stops the whole line -- ch01's own rule, fourteen times over).
# Capper/Filler/Case_Packer are the three busiest wearing parts, so they
# are the three stations this capstone instruments. ProdLine0 is the line
# as commissioned: Filler and Capper each got a few spares in a cabinet
# when the line was built (Custom sparing, <<ch-sparing>>'s TacoTruck1
# story exactly -- RSO=0, nobody re-orders), and Case_Packer runs to
# failure with no PM. ProdLine1 is the same line after the ops review:
# Filler and Capper's cabinets are now on a standing resupply contract
# (RSO=1, restocked every 30 days, with emergency shipping if the shelf
# ever runs dry), and Case_Packer gets scheduled PM. Both files carry
# cost analysis, so the exercise can ask not just "did it get more
# available" but "did it pay for itself." Same two-file "before/after
# instrumenting it properly" shape as ch09's CarCare0/CarCare1.
NO_COST14 = (0.0,) * 14


def prod_line(fname, spared, pmd, costed):
    blks, links = [], []
    lid = 0

    def L(ft, fi, tt, ti):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, ft, fi, tt, ti))

    # (name, fdist, fparams, rdist, rparams)
    stations = [
        ("Rinser", D_EXPO, (3000.0,), D_LOGNORM, (6.0, 2.0)),
        ("Filler", D_WEIBULL, (2.2, 1200.0), D_LOGNORM, (5.0, 1.5)),
        ("Cap_Feeder", D_EXPO, (4000.0,), D_LOGNORM, (6.0, 2.0)),
        ("Capper", D_WEIBULL, (2.5, 1000.0), D_LOGNORM, (6.0, 2.0)),
        ("Cap_Inspector", D_EXPO, (5000.0,), D_LOGNORM, (6.0, 2.0)),
        ("Labeler", D_WEIBULL, (2.0, 1500.0), D_LOGNORM, (6.0, 2.0)),
        ("Date_Coder", D_EXPO, (6000.0,), D_LOGNORM, (6.0, 2.0)),
        ("Carton_Former", D_EXPO, (3500.0,), D_LOGNORM, (6.0, 2.0)),
        ("Case_Packer", D_WEIBULL, (2.3, 1300.0), D_LOGNORM, (5.0, 1.5)),
        ("Case_Sealer", D_EXPO, (4500.0,), D_LOGNORM, (6.0, 2.0)),
        ("Case_Inspector", D_EXPO, (5500.0,), D_LOGNORM, (6.0, 2.0)),
        ("Palletizer", D_WEIBULL, (2.0, 1800.0), D_LOGNORM, (6.0, 2.0)),
        ("Stretch_Wrapper", D_EXPO, (4000.0,), D_LOGNORM, (6.0, 2.0)),
        ("Conveyor_Out", D_EXPO, (6000.0,), D_LOGNORM, (6.0, 2.0)),
    ]
    pm_kw = dict(usespm=1, pmspare=0, pmrefresh=1, pmfailreset=1,
                pmreqdefer=0, pmfreq=600.0, pmdist=D_FIXED, pmparams=(3.0,))
    for i, (nm, fd, fp, rd, rp) in enumerate(stations, 1):
        kw = dict(x=2.5 + 1.5 * i, y=91.25, fdist=fd, fparams=fp,
                  rdist=rd, rparams=rp)
        if nm == "Filler":
            resupply = dict(rso=1, arrive=720.0, eso=1, emertime=48.0) \
                if spared else dict(rso=0)
            kw.update(sptype="Custom", stock=3, newsp=1, **resupply,
                     costs=(0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 500.0, 0.0,
                            0.0, 0.0, 0.0, 0.0, 500.0, 0.0) if costed else NO_COST14)
        elif nm == "Capper":
            resupply = dict(rso=1, arrive=720.0, eso=1, emertime=48.0) \
                if spared else dict(rso=0)
            kw.update(sptype="Custom", stock=2, newsp=1, **resupply,
                     costs=(0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 500.0, 0.0,
                            0.0, 0.0, 0.0, 0.0, 1500.0, 0.0) if costed else NO_COST14)
        elif pmd and nm == "Case_Packer":
            kw.update(**pm_kw,
                     costs=(0.0, 0.0, 0.0, 0.0, 0.0, 80.0, 300.0, 0.0,
                            0.0, 0.0, 50.0, 200.0, 0.0, 0.0) if costed else NO_COST14)
        elif costed:
            kw.update(costs=NO_COST14)
        blks.append(make_block_row(i, nm, **kw))
        if i == 1:
            L("Node", 14, "Block", i)
        else:
            L("Block", i - 1, "Block", i)
    L("Block", len(stations), "Node", 15)
    nodes = [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(15, "end", 2.5 + 1.5 * (len(stations) + 1), 91.25, ntype=3),
    ]
    sys_kw = dict(cost=1, redcost=8000.0) if costed else {}
    write(fname, 8760, 200, "hours", blks, nodes, links, **sys_kw)


prod_line("ProdLine0.rbds", spared=False, pmd=False, costed=True)
prod_line("ProdLine1.rbds", spared=True, pmd=True, costed=True)

# ---------------------------------------------------------------- Flight1
# Capstone III: the reference solution for the open-ended brief (see
# ch-capstone-flight.adoc). One flight, gate to gate, four fixed-length
# phases (Taxi/Climb/Cruise/Approach, D_FIXED so the phase sequence
# always completes in exactly one pass -- a stochastic (D_NORM) phase
# length could wrap back into a second Taxi phase before `term` cuts it
# off) summing to exactly `term`=2.0h, all Mission=1 (single pass, no
# cycling).
# One deliberate cross-mechanic interaction, left for the reader to
# find: Hydraulic_Pump is elementally dependent on Engine_1 (100% Idle,
# the only percentage the engine supports -- see ch-dependency's own
# Limits box) and sits in series after the engine capacity manifold. The
# manifold alone would treat a single dead engine as a mere thrust
# shortfall (Yellow, a lostcost line-item) -- but the hydraulic pump's
# dependency on that same engine forces a full series stop whenever
# Engine_1 specifically (not Engine_2) is the one that dies, deleting
# the redundancy the capacity manifold was built to provide. That is
# ch-dependency's own lesson ("it did not weaken the redundancy a little.
# It deleted it.") hiding inside a bigger model.
def flight1():
    blks, nodes, links = [], [], []
    lid = 0

    def L(*args, **kw):
        nonlocal lid
        lid += 1
        links.append(make_link_row(lid, *args, **kw))

    evs = [make_event_row(1, "Weather_Check", x=2.5, y=91.25, prob=0.98,
                    costs=(0.0, 0.0, 0.0))]

    stress = {"Taxi": 1.000, "Climb": 1.800, "Cruise": 0.600, "Approach": 1.400}
    eng_phases = [f"A: {v:.3f}" for v in stress.values()]
    blks.append(make_block_row(1, "Engine_1", x=6.25, y=93.75,
                         fdist=D_WEIBULL, fparams=(2.0, 8.0),
                         rdist=D_NONE, rparams=(1.0,),
                         phasing=1, phases=eng_phases))
    blks.append(make_block_row(2, "Engine_2", x=6.25, y=88.75,
                         fdist=D_WEIBULL, fparams=(2.0, 8.0),
                         rdist=D_NONE, rparams=(1.0,),
                         phasing=1, phases=eng_phases))
    blks.append(make_block_row(3, "Hydraulic_Pump", x=11.25, y=91.25,
                         fdist=D_EXPO, fparams=(50.0,),
                         rdist=D_FIXED, rparams=(0.05,),
                         depnum=1, deptype="RBDBlock", depidle=100.0))
    blks.append(make_block_row(4, "Main_Generator", x=16.25, y=93.75,
                         fdist=D_WEIBULL, fparams=(2.0, 10.0),
                         rdist=D_NONE, rparams=(1.0,)))
    blks.append(make_block_row(5, "APU_Generator", x=16.25, y=88.75,
                         fdist=D_EXPO, fparams=(15.0,),
                         rdist=D_NONE, rparams=(1.0,)))
    blks.append(make_block_row(6, "Avionics_Suite", x=21.25, y=91.25,
                         fdist=D_EXPO, fparams=(40.0,),
                         rdist=D_FIXED, rparams=(0.02,)))

    nodes += [
        make_node_row(14, "start", 1.25, 91.25, ntype=1),
        make_node_row(16, "split", 3.75, 91.25, ntype=2, k=-1, n=1),
        make_node_row(17, "Thrust_Manifold", 8.75, 91.25, ntype=2,
               k=-1, n=1, out=1, divflow=0, fullflow=0),
        make_node_row(19, "Gen_split", 13.75, 91.25, ntype=2, k=-1, n=1),
        make_node_row(18, "Gen_swap", 18.75, 91.25, ntype=2,
               k=1, n=2, out=1, cold=1, pr=1, autos=1, kstar=1),
        make_node_row(15, "end", 23.75, 91.25, ntype=3),
    ]

    L("Node", 14, "Event", 1)
    L("Event", 1, "Node", 16)
    L("Node", 16, "Block", 1)
    L("Node", 16, "Block", 2)
    L("Block", 1, "Node", 17, cappri=50, nom=75, maxf=100)
    L("Block", 2, "Node", 17, cappri=60, nom=75, maxf=100)
    L("Node", 17, "Block", 3)
    L("Block", 3, "Node", 19)
    L("Node", 19, "Block", 4)
    L("Node", 19, "Block", 5)
    L("Block", 4, "Node", 18, coldpri=50, auto=0.02)
    L("Block", 5, "Node", 18, coldpri=50, auto=0.02)
    L("Node", 18, "Block", 6)
    L("Block", 6, "Node", 15)

    phases = [
        make_phase_row("Taxi", 1, mission=1, dist=D_FIXED, params=(0.20,)),
        make_phase_row("Climb", 2, mission=1, dist=D_FIXED, params=(0.30,)),
        make_phase_row("Cruise", 3, mission=1, dist=D_FIXED, params=(1.30,)),
        make_phase_row("Approach", 4, mission=1, dist=D_FIXED, params=(0.20,)),
    ]
    write("Flight1.rbds", 2.0, 2000, "hours", blks, nodes, links,
          events=evs, phases=phases, analtype="Reliability",
          flow=150, cap=1, cost=1, redcost=50000.0, lostcost=2000.0)


flight1()

print("done")

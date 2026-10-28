"""Build the start page's Advanced example models.

    .venv/bin/python new_docs/tools/gen_advanced_models.py

Larger, realistic systems that show several features working together --
hierarchies, K-of-N voting, pooled spares, preventive maintenance, costs,
and a Rates library that every failure figure is derived from. They are
examples, not tutorials: the User Guide does not walk through them, but
each one runs (`check_models.py` and the docs gate simulate every model in
`new_docs/models/`).

The numbers are typical published reliability figures rounded for
readability, not data for any particular site.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from raptor.api.build import build_from_spec  # noqa: E402
from raptor.core.fileio.rbds import save_rbds  # noqa: E402

OUT = os.path.join(ROOT, "new_docs", "models")

EXP, LOGN, WEIB, FIXED = 4, 7, 15, 19
YEAR = 8760


def _rep(mean: float, sd: float) -> dict:
    """Lognormal repair, mean and standard deviation in hours."""
    return {"repair_dist": LOGN, "repair_params": [mean, sd]}


def hospital_power() -> dict:
    """Emergency power for a hospital's critical loads.

    Utility supply in parallel with a bank of three diesel generators (any
    two carry the load), each on a monthly test-run PM and sharing a pool
    of overhaul kits; two transfer switches; a 2-of-3 UPS with battery
    strings; the main switchboard -- the one part with no backup, as in most
    real installations -- and four distribution panels of which three are
    needed. Every failure figure comes from the Rates library.
    """
    gen = {"fail_dist": WEIB, "fail_params": [1.3, "@GenMTBF", 0],
           **_rep(16, 10), "parent": 100,
           "uses_pm": 1, "pm_freq": 730, "pm_dist": FIXED, "pm_params": [4],
           "sp_type": "Pooled", "spare_pool": "GenOverhaulKits",
           "rep_per_time": 220, "pm_cost": 180, "per_spare": 6500}
    ups = {"fail_dist": EXP, "fail_params": ["@UpsModuleMTBF"],
           **_rep(6, 3), "parent": 200, "rep_per_time": 150}
    return {
        "name": "HospitalPower",
        "system": {"units": "hours", "termination_type": "TimeSim",
                   "term_condition": YEAR, "num_runs": 200,
                   "cost_analysis": True, "weak_analysis": True},
        "rates": [
            {"name": "UtilityMTBF", "expr": "8760 / 3",
             "description": "about three grid outages a year"},
            {"name": "GenMTBF", "expr": "2500",
             "description": "standby diesel set, Weibull scale (hours)"},
            {"name": "AtsMTBF", "expr": "@GenMTBF * 40",
             "description": "transfer switch: far more reliable than a set"},
            {"name": "UpsModuleMTBF", "expr": "6 * 8760",
             "description": "UPS power module, six years"},
            {"name": "BatteryLife", "expr": "4 * 8760",
             "description": "VRLA string, four-year wear-out"},
            {"name": "PanelMTBF", "expr": "25 * 8760",
             "description": "distribution panel and its breakers"},
            {"name": "SwitchboardMTBF", "expr": "@PanelMTBF * 1.5",
             "description": "main LV switchboard: busbars and incomers"},
        ],
        "spare_pools": [
            {"name": "GenOverhaulKits", "stock": 2, "new_spares": 1,
             "arrive_every": 2190, "eso": 1, "emer_time": 72},
        ],
        "blocks": [
            {"name": "Utility_Grid", "fail_dist": EXP,
             "fail_params": ["@UtilityMTBF"], **_rep(3, 4)},
            {"name": "Fuel_Day_Tank", "fail_dist": EXP,
             "fail_params": ["@GenMTBF * 8"], **_rep(8, 4), "parent": 100},
            {"name": "Generator_1", **gen},
            {"name": "Generator_2", **gen},
            {"name": "Generator_3", **gen},
            {"name": "ATS_A", "fail_dist": EXP, "fail_params": ["@AtsMTBF"],
             **_rep(10, 5)},
            {"name": "ATS_B", "fail_dist": EXP, "fail_params": ["@AtsMTBF"],
             **_rep(10, 5)},
            {"name": "UPS_Module_1", **ups},
            {"name": "UPS_Module_2", **ups},
            {"name": "UPS_Module_3", **ups},
            {"name": "Battery_String_A", "fail_dist": WEIB,
             "fail_params": [3.0, "@BatteryLife", 0], **_rep(12, 6),
             "parent": 200, "per_spare": 9000},
            {"name": "Battery_String_B", "fail_dist": WEIB,
             "fail_params": [3.0, "@BatteryLife", 0], **_rep(12, 6),
             "parent": 200, "per_spare": 9000},
            {"name": "Main_Switchboard", "fail_dist": EXP,
             "fail_params": ["@SwitchboardMTBF"], **_rep(24, 12),
             "rep_per_time": 600},
            *({"name": f"Critical_Panel_{i}", "fail_dist": EXP,
               "fail_params": ["@PanelMTBF"], **_rep(5, 2)}
              for i in range(1, 5)),
        ],
        "hierarchies": [
            {"id": 100, "name": "Generator_Bank", "in_id": 11, "out_id": 12},
            {"id": 200, "name": "UPS_System", "in_id": 21, "out_id": 22},
        ],
        "nodes": [
            {"id": 1, "name": "Start", "type": "start"},
            {"id": 2, "name": "Supply_Split", "type": "regular"},
            {"id": 3, "name": "Either_Source", "type": "regular", "k": 1, "n": 2},
            {"id": 4, "name": "Switch_Split", "type": "regular"},
            {"id": 5, "name": "Either_ATS", "type": "regular", "k": 1, "n": 2},
            {"id": 6, "name": "Panel_Split", "type": "regular"},
            {"id": 7, "name": "Panels_3_of_4", "type": "regular", "k": 3, "n": 4},
            {"id": 9, "name": "End", "type": "end"},
            {"id": 11, "name": "Gen_In", "type": "in", "parent": 100},
            {"id": 13, "name": "Gen_Split", "type": "regular", "parent": 100},
            {"id": 14, "name": "Gens_2_of_3", "type": "regular", "k": 2, "n": 3,
             "parent": 100},
            {"id": 12, "name": "Gen_Out", "type": "out", "parent": 100},
            {"id": 21, "name": "UPS_In", "type": "in", "parent": 200},
            {"id": 23, "name": "UPS_Split", "type": "regular", "parent": 200},
            {"id": 24, "name": "Modules_2_of_3", "type": "regular", "k": 2, "n": 3,
             "parent": 200},
            {"id": 25, "name": "Either_Battery", "type": "regular", "k": 1, "n": 2,
             "parent": 200},
            {"id": 26, "name": "UPS_Ready", "type": "regular", "k": 2, "n": 2,
             "parent": 200},
            {"id": 22, "name": "UPS_Out", "type": "out", "parent": 200},
        ],
        "links": [
            {"from": "Start", "to": "Supply_Split"},
            {"from": "Supply_Split", "to": "Utility_Grid"},
            {"from": "Supply_Split", "to": "Gen_In"},
            {"from": "Gen_In", "to": "Fuel_Day_Tank"},
            {"from": "Fuel_Day_Tank", "to": "Gen_Split"},
            *({"from": "Gen_Split", "to": f"Generator_{i}"} for i in (1, 2, 3)),
            *({"from": f"Generator_{i}", "to": "Gens_2_of_3"} for i in (1, 2, 3)),
            {"from": "Gens_2_of_3", "to": "Gen_Out"},
            {"from": "Utility_Grid", "to": "Either_Source"},
            {"from": "Gen_Out", "to": "Either_Source"},
            {"from": "Either_Source", "to": "Switch_Split"},
            {"from": "Switch_Split", "to": "ATS_A"},
            {"from": "Switch_Split", "to": "ATS_B"},
            {"from": "ATS_A", "to": "Either_ATS"},
            {"from": "ATS_B", "to": "Either_ATS"},
            {"from": "Either_ATS", "to": "UPS_In"},
            {"from": "UPS_In", "to": "UPS_Split"},
            *({"from": "UPS_Split", "to": f"UPS_Module_{i}"} for i in (1, 2, 3)),
            *({"from": f"UPS_Module_{i}", "to": "Modules_2_of_3"} for i in (1, 2, 3)),
            {"from": "UPS_Split", "to": "Battery_String_A"},
            {"from": "UPS_Split", "to": "Battery_String_B"},
            {"from": "Battery_String_A", "to": "Either_Battery"},
            {"from": "Battery_String_B", "to": "Either_Battery"},
            {"from": "Modules_2_of_3", "to": "UPS_Ready"},
            {"from": "Either_Battery", "to": "UPS_Ready"},
            {"from": "UPS_Ready", "to": "UPS_Out"},
            {"from": "UPS_Out", "to": "Main_Switchboard"},
            {"from": "Main_Switchboard", "to": "Panel_Split"},
            *({"from": "Panel_Split", "to": f"Critical_Panel_{i}"} for i in range(1, 5)),
            *({"from": f"Critical_Panel_{i}", "to": "Panels_3_of_4"} for i in range(1, 5)),
            {"from": "Panels_3_of_4", "to": "End"},
        ],
    }


def water_treatment() -> dict:
    """A drinking-water treatment works, intake to the town's mains.

    Four stages, each a hierarchy: raw-water intake (2 of 3 pumps behind a
    screen), filtration (3 of 4 filters), disinfection (duty/standby UV
    reactors and a chlorine dosing skid) and high-lift pumping to the town
    (2 of 3). All pumps share one pool of spare motors. The pump figures are
    derived from one rate, so a better pump is one edit.
    """
    pump = {"fail_dist": WEIB, "fail_params": [1.4, "@PumpMTBF", 0],
            **_rep(18, 12), "sp_type": "Pooled", "spare_pool": "SpareMotors",
            "uses_pm": 1, "pm_freq": 2190, "pm_dist": FIXED, "pm_params": [6],
            "rep_per_time": 120, "pm_cost": 400, "per_spare": 4200}

    def stage(hid: int, name: str, nid: int) -> tuple[dict, list[dict]]:
        hier = {"id": hid, "name": name, "in_id": nid, "out_id": nid + 1}
        markers = [
            {"id": nid, "name": f"{name}_In", "type": "in", "parent": hid},
            {"id": nid + 1, "name": f"{name}_Out", "type": "out", "parent": hid},
        ]
        return hier, markers

    stages = [stage(100, "Intake", 10), stage(200, "Filtration", 20),
              stage(300, "Disinfection", 30), stage(400, "High_Lift", 40)]
    blocks = [
        {"name": "Intake_Screen", "fail_dist": EXP,
         "fail_params": ["@ScreenMTBF"], **_rep(6, 3), "parent": 100},
        *({"name": f"Intake_Pump_{i}", **pump, "parent": 100} for i in (1, 2, 3)),
        *({"name": f"Filter_{i}", "fail_dist": WEIB,
           "fail_params": [2.2, "@FilterMTBF", 0], **_rep(36, 12),
           "parent": 200, "rep_per_time": 90} for i in range(1, 5)),
        *({"name": f"UV_Reactor_{x}", "fail_dist": EXP,
           "fail_params": ["@UvMTBF"], **_rep(8, 4), "parent": 300}
          for x in ("A", "B")),
        {"name": "Chlorine_Dosing", "fail_dist": EXP,
         "fail_params": ["@DosingMTBF"], **_rep(4, 2), "parent": 300},
        *({"name": f"High_Lift_Pump_{i}", **pump, "parent": 400} for i in (1, 2, 3)),
        {"name": "Surge_Vessel", "fail_dist": EXP,
         "fail_params": ["@PumpMTBF * 10"], **_rep(24, 12), "parent": 400},
    ]
    nodes = [
        {"id": 1, "name": "Start", "type": "start"},
        {"id": 9, "name": "End", "type": "end"},
        {"id": 12, "name": "Intake_Split", "type": "regular", "parent": 100},
        {"id": 13, "name": "Intake_2_of_3", "type": "regular", "k": 2, "n": 3,
         "parent": 100},
        {"id": 22, "name": "Filter_Split", "type": "regular", "parent": 200},
        {"id": 23, "name": "Filters_3_of_4", "type": "regular", "k": 3, "n": 4,
         "parent": 200},
        {"id": 32, "name": "UV_Split", "type": "regular", "parent": 300},
        {"id": 33, "name": "Either_UV", "type": "regular", "k": 1, "n": 2,
         "parent": 300},
        {"id": 42, "name": "Lift_Split", "type": "regular", "parent": 400},
        {"id": 43, "name": "Lift_2_of_3", "type": "regular", "k": 2, "n": 3,
         "parent": 400},
    ]
    links = [
        {"from": "Start", "to": "Intake_In"},
        {"from": "Intake_In", "to": "Intake_Screen"},
        {"from": "Intake_Screen", "to": "Intake_Split"},
        *({"from": "Intake_Split", "to": f"Intake_Pump_{i}"} for i in (1, 2, 3)),
        *({"from": f"Intake_Pump_{i}", "to": "Intake_2_of_3"} for i in (1, 2, 3)),
        {"from": "Intake_2_of_3", "to": "Intake_Out"},
        {"from": "Intake_Out", "to": "Filtration_In"},
        {"from": "Filtration_In", "to": "Filter_Split"},
        *({"from": "Filter_Split", "to": f"Filter_{i}"} for i in range(1, 5)),
        *({"from": f"Filter_{i}", "to": "Filters_3_of_4"} for i in range(1, 5)),
        {"from": "Filters_3_of_4", "to": "Filtration_Out"},
        {"from": "Filtration_Out", "to": "Disinfection_In"},
        {"from": "Disinfection_In", "to": "UV_Split"},
        {"from": "UV_Split", "to": "UV_Reactor_A"},
        {"from": "UV_Split", "to": "UV_Reactor_B"},
        {"from": "UV_Reactor_A", "to": "Either_UV"},
        {"from": "UV_Reactor_B", "to": "Either_UV"},
        {"from": "Either_UV", "to": "Chlorine_Dosing"},
        {"from": "Chlorine_Dosing", "to": "Disinfection_Out"},
        {"from": "Disinfection_Out", "to": "High_Lift_In"},
        {"from": "High_Lift_In", "to": "Lift_Split"},
        *({"from": "Lift_Split", "to": f"High_Lift_Pump_{i}"} for i in (1, 2, 3)),
        *({"from": f"High_Lift_Pump_{i}", "to": "Lift_2_of_3"} for i in (1, 2, 3)),
        {"from": "Lift_2_of_3", "to": "Surge_Vessel"},
        {"from": "Surge_Vessel", "to": "High_Lift_Out"},
        {"from": "High_Lift_Out", "to": "End"},
    ]
    return {
        "name": "WaterTreatment",
        "system": {"units": "hours", "termination_type": "TimeSim",
                   "term_condition": YEAR, "num_runs": 50,
                   "cost_analysis": True, "weak_analysis": True},
        "rates": [
            {"name": "PumpYears", "expr": "3",
             "description": "centrifugal pump set, Weibull scale in years"},
            {"name": "PumpMTBF", "expr": "@PumpYears * 8760",
             "description": "the same, in hours"},
            {"name": "ScreenMTBF", "expr": "8760 / 2",
             "description": "intake screen blinding, about twice a year"},
            {"name": "FilterMTBF", "expr": "5 * 8760",
             "description": "rapid gravity filter, media wear-out"},
            {"name": "UvMTBF", "expr": "2 * 8760",
             "description": "UV reactor, lamp and ballast failures"},
            {"name": "DosingMTBF", "expr": "8760 / 1.5",
             "description": "chlorine dosing skid"},
        ],
        "spare_pools": [
            {"name": "SpareMotors", "stock": 2, "new_spares": 1,
             "arrive_every": 4380, "eso": 1, "emer_time": 96},
        ],
        "blocks": blocks,
        "hierarchies": [h for h, _ in stages],
        "nodes": nodes + [m for _, ms in stages for m in ms],
        "links": links,
    }


def airliner_mission() -> dict:
    """One twin-engine airliner flight, gate to gate, in five phases.

    Phasing here changes what the flight NEEDS, not just how hard parts
    work -- that is what makes a phased model differ from an unphased one:
    both engines are required for take-off but one will do for the rest
    (the vote node's k changes by phase); the APU generator only helps on
    the ground (cut in the air); the landing gear only matters on the
    ground and works hardest on landing; cabin pressurisation only matters
    in the air. The engines also work harder in the climb.

    The rates are the technical faults that delay, divert or cancel a
    flight (not catastrophic ones), per hour of use, rounded from typical
    figures -- frequent enough that a few thousand simulated flights show
    which phase the trouble comes from.
    """
    PH = ("Taxi_Out", "Takeoff_Climb", "Cruise", "Descent_Landing", "Taxi_In")
    none = {"repair_dist": 18, "repair_params": [1]}
    eng = {"fail_dist": WEIB, "fail_params": [1.5, "@EngineMTBF", 0], **none}
    gen = {"fail_dist": EXP, "fail_params": ["@GeneratorMTBF"], **none}
    pack = {"fail_dist": EXP, "fail_params": ["@PackMTBF"], **none}
    hyd = {"fail_dist": EXP, "fail_params": ["@HydraulicMTBF"], **none}
    return {
        "name": "AirlinerMission",
        "system": {"units": "hours", "termination_type": "TimeSim",
                   "term_condition": 5.0, "num_runs": 1000,
                   "node_anal_type": "Dependability"},
        "rates": [
            {"name": "EngineMTBF", "expr": "600",
             "description": "engine fault that forces a return or diversion"},
            {"name": "GeneratorMTBF", "expr": "@EngineMTBF / 2",
             "description": "engine-driven generator"},
            {"name": "ApuMTBF", "expr": "150",
             "description": "auxiliary power unit and its generator"},
            {"name": "HydraulicMTBF", "expr": "400",
             "description": "one hydraulic system"},
            {"name": "PackMTBF", "expr": "250",
             "description": "one air-conditioning / pressurisation pack"},
            {"name": "GearMTBF", "expr": "300",
             "description": "landing gear, per hour of use"},
        ],
        "phases": [
            {"name": PH[0], "duration_dist": FIXED, "params": [0.25]},
            {"name": PH[1], "duration_dist": FIXED, "params": [0.4]},
            {"name": PH[2], "duration_dist": FIXED, "params": [3.7]},
            {"name": PH[3], "duration_dist": FIXED, "params": [0.5]},
            {"name": PH[4], "duration_dist": FIXED, "params": [0.15]},
        ],
        "blocks": [
            {"name": "Engine_1", **eng}, {"name": "Engine_2", **eng},
            {"name": "Engine_Gen_1", **gen}, {"name": "Engine_Gen_2", **gen},
            {"name": "APU_Generator", "fail_dist": EXP,
             "fail_params": ["@ApuMTBF"], **none},
            {"name": "Hydraulics_A", **hyd}, {"name": "Hydraulics_B", **hyd},
            {"name": "Pack_1", **pack}, {"name": "Pack_2", **pack},
            {"name": "Landing_Gear", "fail_dist": EXP,
             "fail_params": ["@GearMTBF"], **none},
            {"name": "Flight_Controls", "fail_dist": EXP,
             "fail_params": ["@HydraulicMTBF * 20"], **none},
            {"name": "Avionics", "fail_dist": EXP,
             "fail_params": ["@EngineMTBF * 4"], **none},
        ],
        "nodes": [
            {"id": 1, "name": "Gate", "type": "start"},
            {"id": 2, "name": "Engine_Split", "type": "regular"},
            {"id": 3, "name": "Thrust", "type": "regular", "k": 1, "n": 2},
            {"id": 4, "name": "Power_Split", "type": "regular"},
            {"id": 5, "name": "Electrics", "type": "regular", "k": 1, "n": 3},
            {"id": 6, "name": "Hyd_Split", "type": "regular"},
            {"id": 7, "name": "Hydraulics", "type": "regular", "k": 1, "n": 2},
            {"id": 8, "name": "Pack_Split", "type": "regular"},
            {"id": 9, "name": "Cabin_Air", "type": "regular", "k": 1, "n": 2},
            {"id": 10, "name": "Arrival", "type": "end"},
        ],
        "links": [
            {"from": "Gate", "to": "Engine_Split"},
            {"from": "Engine_Split", "to": "Engine_1"},
            {"from": "Engine_Split", "to": "Engine_2"},
            {"from": "Engine_1", "to": "Thrust"},
            {"from": "Engine_2", "to": "Thrust"},
            {"from": "Thrust", "to": "Power_Split"},
            {"from": "Power_Split", "to": "Engine_Gen_1"},
            {"from": "Power_Split", "to": "Engine_Gen_2"},
            {"from": "Power_Split", "to": "APU_Generator"},
            {"from": "Engine_Gen_1", "to": "Electrics"},
            {"from": "Engine_Gen_2", "to": "Electrics"},
            {"from": "APU_Generator", "to": "Electrics"},
            {"from": "Electrics", "to": "Hyd_Split"},
            {"from": "Hyd_Split", "to": "Hydraulics_A"},
            {"from": "Hyd_Split", "to": "Hydraulics_B"},
            {"from": "Hydraulics_A", "to": "Hydraulics"},
            {"from": "Hydraulics_B", "to": "Hydraulics"},
            {"from": "Hydraulics", "to": "Flight_Controls"},
            {"from": "Flight_Controls", "to": "Pack_Split"},
            {"from": "Pack_Split", "to": "Pack_1"},
            {"from": "Pack_Split", "to": "Pack_2"},
            {"from": "Pack_1", "to": "Cabin_Air"},
            {"from": "Pack_2", "to": "Cabin_Air"},
            {"from": "Cabin_Air", "to": "Landing_Gear"},
            {"from": "Landing_Gear", "to": "Avionics"},
            {"from": "Avionics", "to": "Arrival"},
        ],
        # One state per phase: Taxi_Out, Takeoff_Climb, Cruise,
        # Descent_Landing, Taxi_In. "A: s" = active at stress s; "L" =
        # linked, i.e. bypassed -- not needed in that phase; "C" = cut, i.e.
        # out of the system -- the APU is off in the air and cannot help.
        # A node's entry is its k for the phase.
        "phase_states": {
            "Engine_1": ["A: 0.300", "A: 2.500", "A: 0.800", "A: 0.600", "A: 0.300"],
            "Engine_2": ["A: 0.300", "A: 2.500", "A: 0.800", "A: 0.600", "A: 0.300"],
            "Thrust": [1, 2, 1, 1, 1],
            "APU_Generator": ["A: 1.000", "C: 1.000", "C: 1.000", "C: 1.000",
                              "A: 1.000"],
            "Landing_Gear": ["A: 1.000", "A: 1.000", "L: 1.000", "A: 3.000",
                             "A: 1.000"],
            "Pack_1": ["L: 1.000", "A: 1.000", "A: 1.000", "A: 1.000", "L: 1.000"],
            "Pack_2": ["L: 1.000", "A: 1.000", "A: 1.000", "A: 1.000", "L: 1.000"],
        },
    }


def _straighten_home_level(rbd, pitch: float = 3.75) -> None:
    """Put the home level on one line, `pitch` apart, in left-to-right
    order. Auto Layout staggers a run of wide hierarchy ovals up and down
    so they cannot touch; spaced a little wider, a straight line reads
    better and is the shape of the process."""
    cols = (("BLOCK_DATA", "ParentId"), ("NODE_DATA", "parent"),
            ("HIERARCHY_DATA", "Parent"))
    rows = [r for sec, col in cols for r in rbd.section(sec).rows
            if r.get("ID") != "0" and float(r.get(col, 0) or 0) == 0]
    rows.sort(key=lambda r: float(r["xPos"]))
    y = float(rows[0]["yPos"])
    for i, r in enumerate(rows):
        r["xPos"] = f"{1.25 + i * pitch:.6f}"
        r["yPos"] = f"{y:.6f}"
    rbd.view_transform = {}


def main() -> None:
    for spec in (hospital_power(), water_treatment(), airliner_mission()):
        rbd = build_from_spec(spec)
        if spec["name"] == "WaterTreatment":
            _straighten_home_level(rbd)
        path = os.path.join(OUT, spec["name"] + ".rbds")
        save_rbds(rbd, path, meta={"file_name": spec["name"] + ".rbds"})
        print("wrote", path)


if __name__ == "__main__":
    main()

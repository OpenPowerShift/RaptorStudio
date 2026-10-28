"""The example models offered on the File view's Home and New pages.

Each is a model from `new_docs/models/`, with the idea it shows. Its thumbnail is pre-rendered
(`tools/make_example_thumbnails.py`) into
`resources/examples/<stem>_<theme>.png`: rendering every diagram at
launch would take seconds. The File view's New page separates the
first-lesson examples (`EXAMPLES`) from the User Guide's other guided
exercises (`TUTORIALS`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Example:
    file: str          # in new_docs/models/
    title: str
    idea: str          # what it shows, one line
    advanced: bool = False


#: Basics: one idea each, from the User Guide's chapters, in teaching order.
#: Advanced: bigger, realistic systems with several features at once and a
#: Rates library behind the numbers (`new_docs/tools/gen_advanced_models.py`
#: builds the hospital, water and airliner models; the data centre is the
#: guide's capstone).
EXAMPLES: tuple[Example, ...] = (
    Example("Lantern1.rbds", "Camping lantern", "Your first model: parts in series"),
    Example("Espresso1.rbds", "Espresso bar", "Redundancy: two pumps are better than one"),
    Example("Drone1.rbds", "Drone", "Voting: survive losing motors"),
    Example("Bakery1.rbds", "Bakery", "Cold standby: the second oven"),
    Example("Water1.rbds", "Town water", "Capacity: flow through the network"),
    Example("HospitalPower.rbds", "Hospital emergency power",
            "Generators, UPS and PM; every figure from Rates", True),
    Example("WaterTreatment.rbds", "Water treatment works",
            "Four stages, voting pumps, shared spare motors", True),
    Example("DC2.rbds", "Data centre",
            "Power, cooling and IT halls with pooled CRAC spares", True),
    Example("AirlinerMission.rbds", "Airliner flight",
            "Phases change what is needed: both engines for take-off", True),
)

#: Guided exercises from the User Guide, beyond the first-lesson examples.
#: These open only from the New page's "Tutorial Examples" section.
TUTORIALS: tuple[Example, ...] = (
    Example("CarCare0.rbds", "Moped — no PM",
            "Preventive maintenance: the baseline with no schedule"),
    Example("CarCare1.rbds", "Moped — with PM",
            "Preventive maintenance: the same engine on a PM plan"),
    Example("CarWash1.rbds", "Car wash",
            "Phasing: different reliability in each wash cycle"),
    Example("DC1.rbds", "Data centre — baseline",
            "Hierarchies: power, cooling and IT as separate subsystems"),
    Example("EspressoDep1.rbds", "Espresso bar — dependency",
            "Dependencies: a barista failure idles the machine"),
    Example("FinaleShow1.rbds", "Finale show",
            "Events: a one-shot mission event with a probability"),
    Example("Flight1.rbds", "Airliner flight — capstone",
            "Phases, hierarchy, cold standby and a dispatch event together"),
    Example("Laundromat1.rbds", "Laundromat",
            "Transients: Weibull wear-out and a statistics start time"),
    Example("ProdLine0.rbds", "Production line — as built",
            "Spares and resource limits in a long series line"),
    Example("ProdLine1.rbds", "Production line — reviewed",
            "The same line after an ops review and spares changes"),
    Example("TacoTruck1.rbds", "Taco truck",
            "Custom sparing: stock, replenishment and emergency shipping"),
    Example("Toaster_Fixed.rbds", "Toaster — fixed life",
            "Distributions: a constant life, no spread"),
    Example("Toaster_Expo.rbds", "Toaster — exponential",
            "Distributions: constant failure rate, wide spread"),
    Example("Toaster_Weibull.rbds", "Toaster — Weibull",
            "Distributions: wear-out with shape and scale"),
    Example("Van1.rbds", "Delivery van — hierarchical",
            "Hierarchies: subsystems inside the vehicle"),
    Example("VanFlat.rbds", "Delivery van — flat",
            "The same van without hierarchy, for comparison"),
    Example("Vendo1.rbds", "Vending machine",
            "Repair laws: different repair distributions by failure"),
    Example("Water2.rbds", "Town water — with costs",
            "Cost analysis: purchase, repair, downtime and lost flow"),
)

THUMB_DIR = Path(__file__).resolve().parent / "resources" / "examples"


def thumbnail(example: Example, dark: bool) -> Path:
    stem = Path(example.file).stem
    return THUMB_DIR / f"{stem}_{'dark' if dark else 'light'}.png"

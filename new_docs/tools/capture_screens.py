"""Capture tutorial screenshots from RAPTOR Studio (offscreen Qt).

Writes PNGs into new_docs/images/. Run from repo root:
    .venv/bin/python new_docs/tools/capture_screens.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import shutil
import tempfile

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

# Settings in a throwaway folder: the capture must not read or write the
# user's own (recent files, theme, layout), and must look the same on any
# machine.
_SETTINGS = tempfile.mkdtemp(prefix="raptor-capture-")
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS)
QSettings.setDefaultFormat(QSettings.Format.IniFormat)

QDialog.exec = lambda self: QDialog.Accepted
QFileDialog.exec = lambda self: QDialog.Accepted
QMessageBox.question = lambda *a, **k: QMessageBox.Discard
QMessageBox.critical = lambda *a, **k: None
QMessageBox.warning = lambda *a, **k: None
QMessageBox.information = lambda *a, **k: None

IMG = os.path.join(ROOT, "new_docs", "images")
MODELS = os.path.join(ROOT, "new_docs", "models")

app = QApplication(sys.argv)

from raptor.core.fileio.rbd import load_rbd
from raptor.core.fileio.rbds import load_rbds
from raptor.gui.ui.main_window import MainWindow
from raptor.gui import cores as _cores

# The Cores box reads "N of M" for this machine; pin M so the shots are
# the same on any computer.
_cores.available = lambda: 8

w = MainWindow()
w.resize(1600, 1000)
w.show()
app.processEvents()


def load(name):
    # Every tutorial model is native .rbds (JSON) content -- gen_models.py's
    # write() always emits it regardless of the filename passed in. Try
    # .rbds first and fall back to .rbd by filename.
    rbds_path = os.path.join(MODELS, name + ".rbds")
    rbd_path = os.path.join(MODELS, name + ".rbd")
    if os.path.isfile(rbds_path):
        w._load_into_app(load_rbds(rbds_path), rbds_path)
    else:
        w._load_into_app(load_rbd(rbd_path), rbd_path)
    # Every capture should start clean, not carrying over the previous
    # model's Results panel/ribbon-tab state.
    try:
        w._clear_results_action.trigger()
    except Exception:
        pass
    try:
        w._ribbon.tab_bar.setCurrentIndex(w._model_page_index)
    except Exception:
        pass
    app.processEvents()
    try:
        w._fit_action.trigger()
    except Exception:
        pass
    app.processEvents()


def shot(name):
    w.grab().save(os.path.join(IMG, name + ".png"), "PNG")
    print("shot", name)


def trials(n):
    """The run's trial count (the ribbon's Trials box is a read-out)."""
    w._set_exp_field("trials", n)


def tab(attr):
    w._ribbon.tab_bar.setCurrentIndex(getattr(w, attr))
    app.processEvents()


def descend(name):
    v = w._diagram_dock.view
    hid = next(h for h, o in v.hiers.items() if o.name == name)
    v._set_level(hid)
    app.processEvents()
    w._fit_action.trigger()
    app.processEvents()


# 0. the start page, as a first launch shows it. The recent files are
# copies under a stand-in home folder, so the page shows "~/Models/..."
# rather than this machine's paths.
home = tempfile.mkdtemp(prefix="raptor-home-")
os.makedirs(os.path.join(home, "Models"))
recent = []
for name in ("Bakery1", "Espresso1", "Water1", "Van1"):
    dst = os.path.join(home, "Models", name + ".rbds")
    shutil.copy(os.path.join(MODELS, name + ".rbds"), dst)
    recent.append(dst)
from raptor.gui.ui import settings as _settings  # noqa: E402
_settings.set_value("files/recent", recent)
_real_home = os.environ.get("HOME")
os.environ["HOME"] = home
w.show_start_page()
app.processEvents()
shot("start-page")
w._backstage.go_to("New")
app.processEvents()
shot("start-new")
w._backstage.close_view()
if _real_home is not None:
    os.environ["HOME"] = _real_home
app.processEvents()

# 1. empty app, first launch look (after closing the start page)
shot("app-first-look")

# 2. lantern model loaded
load("Lantern1")
shot("lantern-loaded")

# 3. run it, results dock
trials(10)
w._run_simulation()
app.processEvents()
shot("lantern-results")

# 4. espresso redundancy diagram
load("Espresso1")
shot("espresso-loaded")

# 5. drone vote node
load("Drone1")
shot("drone-loaded")

# 6. vendo results with repairs (availability plot)
load("Vendo1")
trials(20)
w._run_simulation()
app.processEvents()
shot("vendo-results")

# 7. data tables dock (Vendo1 still loaded)
try:
    w._data_tables_dock.show()
    w._data_tables_dock.raise_()
    app.processEvents()
    shot("vendo-data-tables")
except Exception as e:
    print("data tables skip:", e)

# 8. ch-dependency: Pump_B's dependency badge, hovered, showing the
# dashed connector to Pump_A (block ids 2 and 1 -- see gen_models.py's
# espresso_dep()). Zoomed and recentred on the pair so the badge glyph
# and the connector are both legible at book image size.
try:
    w._clear_results_action.trigger()  # reclaim canvas height from Vendo1's run
except Exception as e:
    print("clear results skip:", e)
load("EspressoDep1")
v = w._diagram_dock.view
pump_a_item = v._block_items.get(1)
pump_b_item = v._block_items.get(2)
if pump_a_item is not None and pump_b_item is not None:
    v._on_dep_hover(pump_b_item, True)
    app.processEvents()
    w._fit_action.trigger()
    app.processEvents()
    from PySide6.QtCore import QPointF
    mid = QPointF((pump_a_item.pos().x() + pump_b_item.pos().x()) / 2,
                  (pump_a_item.pos().y() + pump_b_item.pos().y()) / 2)
    v.scale(1.4, 1.4)
    v.centerOn(mid)
    app.processEvents()
    shot("espressodep-badge")
else:
    print("espressodep-badge skip: block items not found")

# 9. ch-events: five Shell hexagons feeding a need-3-of-5 vote node.
load("FinaleShow1")
shot("finale-loaded")

# 10. ch-phasing: Track_Motor (plain) + Blower (phased, hatched).
load("CarWash1")
shot("carwash-loaded")

# 11. ch-transients: naive 200h run's Overview tab (Availability ~0.96).
load("Laundromat1")
trials(200)
w._run_simulation()
app.processEvents()
shot("laundromat-naive")

# 12. ch-capstone-line: the 14-station bottling line, just-loaded diagram.
load("ProdLine1")
shot("prodline-loaded")

# 13. ch-capstone-line: ProdLine1 (after the ops review) run, Overview tab.
trials(200)
w._run_simulation()
app.processEvents()
shot("prodline1-results")

# 14. ch-capstone-flight: the reference solution, just-loaded diagram.
load("Flight1")
shot("flight1-loaded")

# 15. ch-capstone-flight: Flight1 run, Overview tab (Reliability ~0.82).
trials(2000)
w._run_simulation()
app.processEvents()
shot("flight1-results")

# 16. ch-sparing: the taco truck.
load("TacoTruck1")
shot("tacotruck-loaded")

# 17. ch-standby: the bakery's two ovens and the cold-swap node.
load("Bakery1")
shot("bakery-loaded")

# 18. ch-preventive: CarCare1 at 20 trials, Overview tab.
load("CarCare1")
trials(20)
w._run_simulation()
app.processEvents()
shot("carcare-results")

# 19-21. ch-hierarchy: the van's home level, then inside each oval.
load("Van1")
shot("van-loaded")
descend("Engine_sub")
shot("van-engine-inside")
descend("Tires_sub")
shot("van-tires-inside")

# 22. ch-capacity: three pumps into the manifold.
load("Water1")
shot("water-loaded")

# 22b. ch-cost: Project Settings is the Inspector page, not a dialog.
load("Water2")
w._open_project_settings()
# A tall window so the whole page fits without scrolling; let the layout
# settle before the grab (one event pass leaves the rows half laid out).
w.resize(1600, 1500)
for _ in range(3):
    app.processEvents()
w._inspector_dock.widget().grab().copy(0, 0, 400, 830).save(
    os.path.join(IMG, "water2-sysconfig.png"), "PNG")
w.resize(1600, 1000)
app.processEvents()
print("shot water2-sysconfig")

# 23-24. ch-capstone (data centre): home level, then inside Power_sub.
load("DC1")
shot("dc-loaded")
descend("Power_sub")
shot("dc-power-inside")

# 25-27. ch-debugger: the bakery, stepped on the Debug tab. The first Step
# is the kickoff at t = 0; stepping on reaches Main_Oven's failure; one
# more step shows the standby carrying.
load("Bakery1")
tab("_debug_page_index")
# Focus Event would centre each shot on the last element that changed and
# crop the ovens; the chapter wants the whole diagram.
w._focus_event_action.setChecked(False)
w._on_step()
app.processEvents()
shot("step-start")
def _logged(text):
    sess = w._step_session
    return sess is None or any(text in ln for ln in sess.logs)


for _ in range(500):
    w._on_step()
    app.processEvents()
    if _logged("Failed"):
        break
# Step pauses on each change, so finish the failure's instant: Repairing,
# the swap node Bad, the system's State_change Red (what the chapter shows).
for _ in range(30):
    if _logged("State_change"):
        break
    w._on_step()
    app.processEvents()
shot("step-fail")
w._on_step()
app.processEvents()
shot("step-ribbon")
w._terminate_action.trigger()
app.processEvents()

# 28-29. ch06: the Simulate tab with the Cores box, and a finished Sweep.
load("Espresso1")
w._live_sim_action.setChecked(False)
tab("_simulate_page_index")
shot("simulate-cores")
sweep = w._open_sweep_dialog()
sweep.select("Block  Pump_A", "Failure") or sweep.select("Block", "Failure")
sweep._points.setValue(5)
sweep._trials.setValue(200)
# One worker for the capture itself: this script has no __main__ guard,
# and spawned workers would re-run it. The plan line keeps "8 of 8".
_chosen = _cores.chosen
_cores.chosen = lambda: 1
sweep._start()
while not sweep._job.done:
    app.processEvents()
sweep._poll()
_cores.chosen = _chosen
app.processEvents()
sweep.grab().save(os.path.join(IMG, "sweep-dialog.png"), "PNG")
print("shot sweep-dialog")
sweep.close()

print("done")

"""Failure Effects View colours and links mean what they mean in Raptor 7.

`tests/data/fev_colours_raptor7.json` holds, per model and per sequence of clicks, the colour CLASS Raptor 7 drew
for each element and link (read from its own screenshots, 2026-10-11): green running, red failed or down, amber PM,
brown idle, yellow degraded node, blue standby, "resting" for a link between elements that are up. This replays the
same clicks in the port and requires the same class for every element. The port may style a class differently
(its own greens and a slate resting colour) -- the meaning is what is pinned."""
from __future__ import annotations

import json
import pathlib

import pytest
from PySide6.QtWidgets import QApplication

from raptor.core.fileio.rbd import load_rbd
from raptor.gui.resources.design_tokens import APPENDIX_D_STATE_COLOURS, LIGHT_COLOUR
from raptor.gui.ui.diagram_live import LiveOverlayMixin

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = json.loads((ROOT / "tests" / "data" / "fev_colours_raptor7.json").read_text())
PATHS = {"Phasing3": ROOT / "tests" / "golden" / "Phasing3" / "input.rbd",
         "DEPDOWN": ROOT / "tests" / "fixtures" / "DEPDOWN" / "input.rbd",
         "NodeStandbyProbe": ROOT / "tests" / "golden" / "NodeStandbyProbe" / "input.rbd",
         "FevPhasing3": ROOT / "tests" / "golden" / "FevPhasing3" / "input.rbd"}
LETTERS = {"A": "BlockA", "B": "BlockB"}


def _palette():
    M = LiveOverlayMixin
    return {"green": APPENDIX_D_STATE_COLOURS["running"], "red": APPENDIX_D_STATE_COLOURS["repairing"],
            "amber": APPENDIX_D_STATE_COLOURS["maintenance"], "brown": APPENDIX_D_STATE_COLOURS["idle"],
            "yellow": M._NODE_STATE_COLOURS["Degraded"], "blue": APPENDIX_D_STATE_COLOURS["warm_standby"],
            "resting": LIGHT_COLOUR["muted"],
            "over": M._STRESS_COLOURS["over"], "under": M._STRESS_COLOURS["under"]}


def _rgb(hexs: str) -> tuple[int, int, int]:
    h = hexs.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _classify(colour) -> str:
    pal = {k: _rgb(v) for k, v in _palette().items()}
    got = (colour.red(), colour.green(), colour.blue())
    return min(pal, key=lambda k: sum((a - b) ** 2 for a, b in zip(pal[k], got)))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _window(model: str, qapp):
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1500, 900)
    w._load_into_app(load_rbd(str(PATHS[model])), None)
    qapp.processEvents()
    assert w._enter_fev()
    return w


def _classes(w) -> dict[str, str]:
    v = w._diagram_dock.view

    def nm(kind, i):
        o = {"block": v.blocks, "node": v.nodes, "hier": v.hiers, "event": v.events}.get(kind, {}).get(i)
        return getattr(o, "name", None) or getattr(o, "node_name", None) or str(i)
    kind_of = {"RBDBlock": "block", "Block": "block", "RBDNode": "node", "Node": "node", "RBDHier": "hier", "Hier": "hier",
               "RBDEvent": "event", "Event": "event"}
    out: dict[str, str] = {}
    for items, kind in ((v._block_items, "block"), (v._node_items, "node"), (v._hier_items, "hier")):
        for i, it in items.items():
            out[nm(kind, i)] = _classify(it.brush().color())
    num_to_name = {}
    for lnk in v.links:
        num_to_name[lnk.object_num] = (f"{nm(kind_of.get(lnk.connect_f_ref, 'node'), lnk.connect_from_id)}->"
                                       f"{nm(kind_of.get(lnk.connect_t_ref, 'node'), lnk.connect_to_id)}")
    for it in v._links:
        out[num_to_name[it._link_num]] = _classify(it.pen().color())
    return out


def _replay(w, clicks: str, qapp) -> None:
    s = w._fev
    for token in [t for t in clicks.split(",") if t]:
        name = LETTERS.get(token, token)
        bid = next(b.id for b in s.model.blocks_by_id.values() if b.name == name)
        w._fev_apply("click", "block", bid, name)
    qapp.processEvents()


CASES = [(m, c) for m, d in DATA.items() if isinstance(d, dict) and "clicks" in d for c in d["clicks"]]
TIMES = list(DATA["Phasing3_stepping"]["time"])


@pytest.mark.parametrize("model,clicks", CASES, ids=[f"{m}[{c or 'start'}]" for m, c in CASES])
def test_the_port_draws_the_same_colour_classes_as_raptor_7(qapp, model, clicks):
    w = _window(model, qapp)
    _replay(w, clicks, qapp)
    got = _classes(w)
    want = DATA[model]["clicks"][clicks]
    wrong = {k: (want[k], got.get(k)) for k in want if got.get(k) != want[k]}
    assert not wrong, f"element: (Raptor 7, port) -> {wrong}"


def test_inside_a_failed_hierarchy_the_links_are_red_like_raptor_7(qapp):
    w = _window("FevPhasing3", qapp)
    _replay(w, "SolarP_650W", qapp)
    v = w._diagram_dock.view
    hid = next(i for i, h in v.hiers.items() if h.name == "650path")
    v.enter_level(hid)
    qapp.processEvents()
    w._fev_refresh()
    got = _classes(w)
    want = DATA["FevPhasing3"]["inner_650path_after_SolarP_650W"]
    wrong = {k: (want[k], got.get(k)) for k in want if got.get(k) != want[k]}
    assert not wrong, f"element: (Raptor 7, port) -> {wrong}"


@pytest.mark.parametrize("when", TIMES, ids=[f"t={t}" for t in TIMES])
def test_the_stepping_debugger_draws_the_same_classes_as_raptor_7_on_phasing3(qapp, when):
    """Stepping Phasing3 (phases, hierarchies, events, standby, stress): the state after the last event at each
    captured simulation time. Stress shades and the link rule are the points that were wrong before."""
    from raptor.gui.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1500, 900)
    w._load_into_app(load_rbd(str(PATHS["Phasing3"])), None)
    w._run_count_spin.setValue(1)
    w._live_sim_action.setChecked(True)
    w._on_step()
    qapp.processEvents()
    sess = w._step_session
    target = float(when)
    last = None
    for _ in range(600):
        if sess.done or sess.time > target + 1e-6:
            break
        sess.step()
        if abs(sess.time - target) < 2e-6:
            w._refresh_live_step_view(sess)
            qapp.processEvents()
            last = _classes(w)
    if target == 0.0:
        w._refresh_live_step_view(sess)
        last = _classes(w)
    assert last is not None, "the port never reached that simulation time"
    want = DATA["Phasing3_stepping"]["time"][when]
    wrong = {k: (want[k], last.get(k)) for k in want if last.get(k) != want[k]}
    assert not wrong, f"element: (Raptor 7, port) -> {wrong}"

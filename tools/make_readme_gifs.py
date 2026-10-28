#!/usr/bin/env python3
"""Regenerate the README's animated GIFs from the running application.

    QT_QPA_PLATFORM=offscreen python tools/make_readme_gifs.py [tour] [mcp]

`tour` drives the real main window (offscreen Qt) through a model, an edit panel, a run, the results, the weak-link
colouring and the dark theme, grabbing the window at each step. `mcp` calls the real MCP server's tools in process
and replays the calls and their (shortened) replies as a terminal recording. Nothing is mocked: every number in a
frame came out of the engine while the script ran.

Writes docs/readme/app-tour.gif and docs/readme/mcp-demo.gif.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "readme"

from PIL import Image  # noqa: E402

TOUR_WIDTH = 1100          # pixels in the GIF
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def _save_gif(frames: list[Image.Image], durations: list[int], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    quant = [f.convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
             for f in frames]
    quant[0].save(path, save_all=True, append_images=quant[1:], duration=durations, loop=0, optimize=True,
                  disposal=1)
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KiB, {len(frames)} frames)")


# ----------------------------------------------------------------------------------------------------------
# the application tour
# ----------------------------------------------------------------------------------------------------------
def make_tour() -> None:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QDialog

    QDialog.exec = lambda self: QDialog.DialogCode.Accepted      # no modal dialog may block the script
    # Throwaway settings: the tour must not read or write the user's own
    # (recent files, theme), and must look the same on any machine.
    import tempfile
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      tempfile.mkdtemp(prefix="raptor-tour-"))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    app = QApplication.instance() or QApplication([])
    from raptor.core.fileio.rbds import load_rbds
    from raptor.gui.ui.main_window import MainWindow
    from raptor.gui.resources.design_tokens import ThemeManager

    w = MainWindow()
    w.resize(1440, 880)
    w.show()
    app.processEvents()
    frames: list[Image.Image] = []
    durations: list[int] = []

    def grab(caption: str, ms: int = 2600) -> None:
        app.processEvents()
        image = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont("DejaVu Sans")
        font.setPixelSize(30)
        font.setBold(True)
        p.setFont(font)
        band_h, margin = 62, 24
        rect = QRectF(0, image.height() - band_h - margin, image.width(), band_h)
        p.fillRect(rect, QColor(18, 28, 44, 232))
        p.setPen(QColor(255, 255, 255))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), caption)
        p.end()
        path = OUT / "_frame.png"
        OUT.mkdir(parents=True, exist_ok=True)
        image.save(str(path))
        im = Image.open(path).convert("RGB")
        im.thumbnail((TOUR_WIDTH, 10_000), Image.Resampling.LANCZOS)
        frames.append(im.copy())
        durations.append(ms)
        print("frame:", caption)

    # The start page, as a first launch shows it.
    w.show_start_page()
    w._backstage.go_to("New")
    grab("Start from a blank model or a worked example", 2800)
    w._backstage.close_view()

    path = ROOT / "new_docs" / "models" / "HospitalPower.rbds"
    w._load_into_app(load_rbds(str(path)), str(path))
    for dock in (w._explorer_dock, w._inspector_dock):
        dock.hide()
    app.processEvents()
    w._fit_action.trigger()
    grab("Draw the system: blocks, nodes, events, hierarchies, mission phases", 3000)

    w._inspector_dock.show()
    app.processEvents()
    w._fit_action.trigger()
    v = w._diagram_dock.view
    item = next((it for bid, it in v._block_items.items()
                 if v.blocks[bid].name == "Utility_Grid"), None)
    if item is not None:
        item.setSelected(True)
        app.processEvents()
        grab("Describe how each part fails, is repaired and is spared", 3200)
        item.setSelected(False)

    w._run_panel._analysis_checks["weak"].setChecked(True)
    w._run_panel._recording_checks["ao_plot"].setChecked(True)
    w._run_panel._ao_interval.setValue(100.0)
    w._run_panel._ao_interval.editingFinished.emit()
    w._set_exp_field("trials", 500)
    w._run_simulation()
    for dock in (w._explorer_dock, w._inspector_dock):
        dock.hide()
    app.processEvents()
    w._fit_action.trigger()
    grab("Run hundreds of simulated futures", 3200)

    from PySide6.QtWidgets import QTabWidget
    tabs = [t for t in w._results.findChildren(QTabWidget) if t.tabText(0) == "Overview"]
    if tabs:
        for name, caption, ms in (("Final Results", "Read the numbers an engineer needs", 3200),
                                  ("Graphs", "Plot availability over time", 3200)):
            for i in range(tabs[0].count()):
                if tabs[0].tabText(i) == name:
                    tabs[0].setCurrentIndex(i)
                    app.processEvents()
                    w._fit_action.trigger()
                    grab(caption, ms)

    ThemeManager.instance().set_dark(True)
    app.processEvents()
    grab("Light and dark themes; every panel dockable", 2800)
    ThemeManager.instance().set_dark(False)

    frames.append(frames[0])
    durations.append(1200)
    _save_gif(frames, durations, OUT / "app-tour.gif")
    (OUT / "_frame.png").unlink(missing_ok=True)


# ----------------------------------------------------------------------------------------------------------
# the MCP session
# ----------------------------------------------------------------------------------------------------------
PUMP = {"fail_dist": 4, "fail_params": [2000.0, 0], "repair_dist": 4, "repair_params": [24.0, 0]}
COOLING = {
    "system": {"term_condition": 8760, "num_runs": 200},
    "blocks": [dict(PUMP, id=1, name="Pump_A"), dict(PUMP, id=2, name="Pump_B"),
               {"id": 3, "name": "Heat_Exchanger", "fail_dist": 4, "fail_params": [8000.0, 0], "repair_dist": 4,
                "repair_params": [48.0, 0]}],
    "nodes": [{"id": 10, "name": "Start", "type": "start"},
              {"id": 13, "name": "Pumps_in", "type": "regular"},
              {"id": 11, "name": "Pumps_out", "type": "regular", "k": 1},
              {"id": 12, "name": "End", "type": "end"}],
    "links": [{"from": "Start", "to": "Pumps_in"}, {"from": "Pumps_in", "to": "Pump_A"},
              {"from": "Pumps_in", "to": "Pump_B"},
              {"from": "Pump_A", "to": "Pumps_out"}, {"from": "Pump_B", "to": "Pumps_out"},
              {"from": "Pumps_out", "to": "Heat_Exchanger"}, {"from": "Heat_Exchanger", "to": "End"}],
}
PROMPT = ("Two pumps in parallel feed a heat exchanger. Pumps: MTBF 2000 h, 24 h to repair. Exchanger: MTBF "
          "8000 h, 48 h to repair. Over a year, is availability above 99 %?")


def mcp_session() -> list[tuple[str, str, list[str]]]:
    """Drive the real MCP tools; returns (call text, kind, result lines) per step."""
    import asyncio
    import tempfile

    from raptor.mcp import tools_model, tools_run, tools_tables, tools_view  # noqa: F401  (registers the tools)
    from raptor.mcp.server import mcp

    mcp._raptor_args = type("A", (), {"workspace": tempfile.mkdtemp(), "read_only": False})()
    mcp._raptor_ws = None

    def call(_tool: str, **args):
        res = asyncio.run(mcp.call_tool(_tool, args))
        return res.structured_content

    steps: list[tuple[str, str, list[str]]] = []
    built = call("build_model", name="cooling", spec=COOLING)
    mid = built["model_id"]
    steps.append(('build_model(name="cooling", spec={3 blocks, 4 nodes, 7 links})', "call",
                  [f"model_id {mid}  -  {built['summary']}  -  "
                   f"{'runnable' if built['validation']['runnable'] else 'NOT runnable'}"]))
    sim = call("simulate", model_id=mid, num_runs=200)
    a = sim["summary"]["Availability"]
    steps.append((f'simulate(model_id="{mid}", num_runs=200)', "call",
                  [f"{sim['runs']} runs in {sim['wall_seconds']:.1f} s  -  Availability {a['mean']:.4f} "
                   f"(min {a['min']:.3f}, max {a['max']:.3f})  -  MTBDE {sim['summary']['MTBDE']['mean']:.0f} h"]))
    spf = call("single_point_failures", model_id=mid)
    steps.append((f'single_point_failures(model_id="{mid}")', "call",
                  [f"{spf['count']} single point of failure: " + ", ".join(r["name"] for r in spf["spofs"])]))
    sw = call("sweep_parameter", model_id=mid, selector="Heat_Exchanger", field="fail_params",
              values=[[4000.0, 0], [8000.0, 0], [16000.0, 0]], num_runs=100)
    lines = [f"MTBF {int(r['value'][0]):>6} h   Availability {r['Availability']:.4f}   "
             f"MTBDE {r['MTBDE']:.0f} h" for r in sw["rows"]]
    steps.append(('sweep_parameter(selector="Heat_Exchanger", field="fail_params", values=[4000, 8000, 16000])',
                  "call", lines))
    return steps


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    import textwrap
    return textwrap.wrap(text, width, subsequent_indent=indent) or [""]


def make_mcp() -> None:
    from PIL import ImageDraw, ImageFont

    steps = mcp_session()
    avail = float(steps[1][2][0].split("Availability ")[1].split(" ")[0])
    sweep = steps[3][2]
    end = (f"Yes: {avail:.4f} over a year, above 99 %. The exchanger is the only single point of failure, and "
           f"doubling its MTBF moves availability from {sweep[1].split('Availability ')[1].split()[0]} to "
           f"{sweep[2].split('Availability ')[1].split()[0]}.")

    W, H, PAD, LH, SIZE, COLS = 1100, 640, 24, 24, 16, 108
    font = ImageFont.truetype(MONO, SIZE)
    bold = ImageFont.truetype(MONO.replace("Mono", "Mono-Bold"), SIZE)
    cap_font = ImageFont.truetype(FONT.replace("Sans.ttf", "Sans-Bold.ttf"), 20)
    colours = {"you": (130, 170, 255), "tool": (127, 219, 202), "res": (173, 219, 103), "ai": (247, 140, 108),
               "dim": (110, 124, 150), "fg": (214, 222, 235)}

    #: (label, colour, text) blocks in order; each is typed or shown whole.
    script: list[tuple[str, str, list[str], bool]] = [
        ("you", "you", _wrap(PROMPT, COLS - 12, " " * 12), True)]
    for call_text, _kind, res in steps:
        script.append(("call", "tool", _wrap(call_text, COLS - 12, " " * 12), True))
        script.append(("result", "res", res, False))
    script.append(("assistant", "ai", _wrap(end, COLS - 12, " " * 12), True))
    labels = {"you": "You", "call": "tool call", "result": "  result", "assistant": "Assistant"}

    def render(visible: list[tuple[str, str, list[str]]], caption: str) -> Image.Image:
        image = Image.new("RGB", (W, H), (13, 20, 32))
        d = ImageDraw.Draw(image)
        # title bar
        d.rectangle([0, 0, W, 34], fill=(27, 38, 58))
        for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            d.ellipse([16 + i * 22, 11, 28 + i * 22, 23], fill=c)
        d.text((W // 2, 17), "an AI assistant talking to the RAPTOR Studio MCP server", fill=colours["dim"],
               font=font, anchor="mm")
        rows: list[tuple[str, tuple, str]] = []
        for label, col, lines in visible:
            for i, ln in enumerate(lines):
                rows.append((labels[label] if i == 0 else "", colours[col], ln))
            if label != "call":
                rows.append(("", colours["fg"], ""))
        room = (H - 34 - 60 - PAD) // LH
        rows = rows[-room:]
        y = 34 + PAD // 2
        for lab, col, ln in rows:
            d.text((PAD, y), lab, fill=colours["dim"], font=font)
            d.text((PAD + 12 * 9.6, y), ln[:COLS - 12], fill=col, font=font)
            y += LH
        d.rectangle([0, H - 52, W, H], fill=(27, 38, 58))
        d.text((W // 2, H - 26), caption, fill=(255, 255, 255), font=cap_font, anchor="mm")
        return image

    cap = "Describe a system in words; the tools build, validate and simulate it"
    cap_done = "Real tool calls and results from the running server; the narration is scripted"
    frames: list[Image.Image] = []
    dur: list[int] = []
    shown: list[tuple[str, str, list[str]]] = []

    def typed(label: str, col: str, lines: list[str]) -> None:
        full = "\n".join(lines)
        for frac in (0.25, 0.5, 0.75, 1.0):
            part = full[: max(1, int(len(full) * frac))].split("\n")
            frames.append(render(shown + [(label, col, part)], cap))
            dur.append(110)
        shown.append((label, col, lines))
        dur[-1] = 700

    frames.append(render([], cap))
    dur.append(800)
    for label, col, lines, is_typed in script:
        if is_typed:
            typed(label, col, lines)
        else:
            shown.append((label, col, lines))
            frames.append(render(shown, cap))
            dur.append(1700 if len(lines) > 1 else 1300)
    frames.append(render(shown, cap_done))
    dur.append(4200)
    _save_gif(frames, dur, OUT / "mcp-demo.gif")


if __name__ == "__main__":
    which = sys.argv[1:] or ["tour", "mcp"]
    if "tour" in which:
        make_tour()
    if "mcp" in which:
        make_mcp()

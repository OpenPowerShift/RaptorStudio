#!/usr/bin/env python3
"""Regenerate the README's animated GIFs from the running application.

    QT_QPA_PLATFORM=offscreen python tools/make_readme_gifs.py [tour] [mcp]
    QT_QPA_PLATFORM=offscreen python tools/make_readme_gifs.py --check [tour] [mcp]

`tour` drives the real main window (offscreen Qt) through a model, an edit panel, a run, the results, the weak-link
colouring and the dark theme, grabbing the window at each step. `mcp` calls the real MCP server's tools in process
and replays the calls and their (shortened) replies as a terminal recording. Nothing is mocked: every number in a
frame came out of the engine while the script ran.

`--check` writes into a temporary directory and compares the result with
the committed GIF instead of overwriting it. It drives the same GUI/MCP
session, so it is a long/manual gate. The default check covers the tour;
`mcp-demo.gif` embeds the measured simulation wall time and therefore is
not byte-compared.

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

    def grab(caption: str, ms: int = 2600, dialog=None) -> None:
        app.processEvents()
        image = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if dialog is not None:
            # an offscreen dialog is its own window: draw it over the main
            # window, centred, on a dimmed backdrop, as a user would see it
            p.fillRect(image.rect(), QColor(0, 0, 0, 90))
            shot = dialog.grab().toImage()
            x = (image.width() - shot.width()) // 2
            y = max(10, (image.height() - shot.height()) // 2 - 30)
            p.fillRect(x + 6, y + 8, shot.width(), shot.height(), QColor(0, 0, 0, 70))
            p.drawImage(x, y, shot)
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

    # The start page, as a first launch shows it (with its "Did you know?" tip).
    w.show_start_page()
    w._backstage.go_to("Home")
    grab("Start from a blank model or a worked example; a new tip each launch", 2800)
    w._backstage.close_view()

    path = ROOT / "new_docs" / "models" / "WaterTreatment.rbds"
    w._load_into_app(load_rbds(str(path)), str(path))
    side = (w._explorer_dock, w._library_dock, w._inspector_dock, w._advisor_dock)
    for dock in side:
        dock.hide()
    app.processEvents()
    w._fit_action.trigger()
    grab("Draw the system: blocks, nodes, events, hierarchies, mission phases", 3000)

    w._inspector_dock.show()
    app.processEvents()
    w._fit_action.trigger()
    v = w._diagram_dock.view
    # open the Intake hierarchy and select its screen
    if v.locate_element("block", "Intake_Screen"):
        app.processEvents()
        v.scene().clearSelection()
        for bid, it in v._block_items.items():
            if v.blocks[bid].name == "Intake_Screen":
                it.setSelected(True)
        import time
        settle = time.monotonic() + 1.0      # the Inspector follows the selection on a timer
        while time.monotonic() < settle:
            app.processEvents()
            time.sleep(0.02)
        w._fit_action.trigger()
        grab("Open a hierarchy; describe how each part fails, is repaired and is spared", 3200)
        v.scene().clearSelection()
        v.go_home()
        app.processEvents()

    w._show_advisor()
    w._refresh_advisor()
    app.processEvents()
    w._fit_action.trigger()
    grab("The Advisor flags likely mistakes as you build, with a fix for each", 3400)

    w._run_panel._analysis_checks["weak"].setChecked(True)
    w._run_panel._recording_checks["ao_plot"].setChecked(True)
    w._run_panel._ao_interval.setValue(100.0)
    w._run_panel._ao_interval.editingFinished.emit()
    w._set_exp_field("trials", 500)
    w._confirm_long_batch = lambda n: True
    w._run_simulation()
    for dock in side:
        dock.hide()
    app.processEvents()
    w._fit_action.trigger()

    from PySide6.QtWidgets import QTabWidget
    tabs = [t for t in w._results.findChildren(QTabWidget) if t.tabText(0) == "Overview"]

    def tab(name: str) -> bool:
        for i in range(tabs[0].count() if tabs else 0):
            if tabs[0].tabText(i) == name:
                tabs[0].setCurrentIndex(i)
                app.processEvents()
                w._fit_action.trigger()
                return True
        return False

    if tab("Overview"):
        grab("Run hundreds of simulated futures; the Story says what happened, in words", 3600)
    if tab("Drivers"):
        w._results._refresh_drivers()
        grab("Drivers: which parts cost the most uptime, and how sure that is", 3400)
    w._downtime_overlay_action.setChecked(True)
    bottom = [d for d in (w._results_dock, w._run_dock, w._data_tables_dock,
                          w._live_log_dock) if d.isVisible()]
    for dock in bottom:
        dock.hide()
    app.processEvents()
    w._fit_action.trigger()
    grab("Colour the diagram by each part's share of the downtime", 3200)
    w._downtime_overlay_action.setChecked(False)
    for dock in bottom:
        dock.show()
    w._results_dock.raise_()
    app.processEvents()
    if tab("Final Results"):
        grab("Read the numbers an engineer needs", 2800)
    if tab("Charts"):
        grab("Charts of availability, downtime, cost and capacity through the run", 3000)

    def study(dlg) -> None:
        import time
        dlg.show()
        dlg.start()
        end = time.monotonic() + 300
        while dlg._job is not None and not dlg._job.done and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.05)
        dlg._poll()
        app.processEvents()

    dlg = w._open_whatif_dialog()
    dlg._block.setCurrentText("Intake_Screen")
    for preset in ("half the repair time", "one more spare"):
        if dlg._preset.findText(preset) >= 0:
            dlg._preset.setCurrentText(preset)
            dlg._add_preset()
    dlg._trials.setValue(200)
    study(dlg)
    grab("What If: try a change on the same random futures, so the gain is real", 3800, dialog=dlg)
    dlg.hide()

    dlg = w._open_sensitivity_dialog()
    dlg._top.setValue(8)
    dlg._trials.setValue(200)
    study(dlg)
    grab("Sensitivity: which inputs are worth getting right", 3400, dialog=dlg)
    dlg.hide()

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

    from raptor.mcp import (  # noqa: F401  (registers the tools)
        tools_advisor, tools_model, tools_run, tools_tables, tools_view,
    )
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
    sim = call("simulate", model_id=mid, num_runs=200, attribution=True)
    a = sim["summary"]["Availability"]
    steps.append((f'simulate(model_id="{mid}", num_runs=200, attribution=true)', "call",
                  [f"{sim['runs']} runs in {sim['wall_seconds']:.1f} s  -  Availability {a['mean']:.4f} "
                   f"(min {a['min']:.3f}, max {a['max']:.3f})  -  MTBDE {sim['summary']['MTBDE']['mean']:.0f} h"]))
    spf = call("single_point_failures", model_id=mid)
    steps.append((f'single_point_failures(model_id="{mid}")', "call",
                  [f"{spf['count']} single point of failure: " + ", ".join(r["name"] for r in spf["spofs"])]))
    drivers = call("downtime_drivers", run_id=sim["run_id"], top=2)
    lines = [f"{e['name']:<15} {100 * e['dtci']['mean']:5.1f} % of the downtime "
             f"(+/- {100 * e['dtci']['sem']:.1f}), mostly {e.get('main_cause') or '-'}"
             for e in drivers["elements"][:2]]
    steps.append((f'downtime_drivers(run_id="{sim["run_id"]}", top=2)', "call", lines))
    rec = call("recommend_changes", model_id=mid, run_id=sim["run_id"], top=2, trials=200)
    lines = [f"{r['name']:<49} +{100 * r['gain']['availability']:.2f} pts "
             f"(+/- {100 * r['gain']['sem']:.2f})  {r['verdict']}"
             for r in rec["rows"][:3] if r.get("gain")]
    steps.append((f'recommend_changes(model_id="{mid}", top=2, trials=200)', "call", lines))
    return steps


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    import textwrap
    return textwrap.wrap(text, width, subsequent_indent=indent) or [""]


def make_mcp() -> None:
    from PIL import ImageDraw, ImageFont

    steps = mcp_session()
    avail = float(steps[1][2][0].split("Availability ")[1].split(" ")[0])
    top = steps[3][2][0]
    best = steps[4][2][0] if steps[4][2] else ""
    share = top.split("%")[0].split()[-1]
    end = (f"Yes: {avail:.4f} over a year, above 99 %. The heat exchanger is the only single point of failure "
           f"and causes {share} % of the downtime. The best tested fix is to "
           f"{best.split(':', 1)[1].split('+')[0].strip() if ':' in best else 'improve it'}: "
           f"{best.split('pts')[0].split()[-1] if 'pts' in best else ''} availability points, measured on the "
           f"same random futures as the base, so the gain is real.")

    W, H, PAD, LH, SIZE, COLS = 1100, 700, 24, 24, 16, 108
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

    cap = "Describe a system in words; the tools build, simulate and explain it"
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
    import tempfile

    args = sys.argv[1:]
    check = "--check" in args
    which = [a for a in args if not a.startswith("--")]
    if not which:
        # `--check` defaults to the tour: the MCP demo prints the measured
        # simulation wall time, so two runs are never byte-identical.
        which = ["tour"] if check else ["tour", "mcp"]
    committed = OUT
    if check:
        OUT = Path(tempfile.mkdtemp(prefix="raptor-readme-gifs-"))
    try:
        if "tour" in which:
            make_tour()
        if "mcp" in which:
            make_mcp()
        if check:
            stale = []
            for gif in sorted(OUT.glob("*.gif")):
                if gif.name == "mcp-demo.gif":
                    print("make_readme_gifs.py: note: mcp-demo.gif shows the "
                          "measured run time and is not byte-compared.",
                          file=sys.stderr)
                    continue
                expected = committed / gif.name
                if (not expected.is_file()
                        or expected.read_bytes() != gif.read_bytes()):
                    stale.append(gif.name)
            if stale:
                print(f"make_readme_gifs.py: {len(stale)} stale GIF(s): "
                      + ", ".join(stale), file=sys.stderr)
                sys.exit(1)
            print("make_readme_gifs.py: OK -- every comparable committed GIF "
                  "matches a fresh run.")
    finally:
        if check:
            import shutil
            shutil.rmtree(OUT, ignore_errors=True)

"""Diagram render worker and its Qt-free API wrapper.

The worker runs in a *separate process* (PySide6 is not imported by this
module), so these tests exercise the real subprocess path. They check the
PNG is non-blank (more than one distinct pixel colour) and within the
``max_px`` cap, and that every failure mode degrades to a typed error.

PNG decoding uses the stdlib (``zlib``/``struct``) rather than Pillow,
which is not a declared dependency, or Qt, which must not be imported
here.
"""
from __future__ import annotations

import inspect
import re
import struct
import zlib
from pathlib import Path

import pytest

from raptor.api import render
from raptor.api.render import (
    RenderError,
    RenderFailed,
    RenderOutputMissing,
    RenderTimeout,
    RenderUnavailable,
    render_diagram,
)

REPO = Path(__file__).resolve().parent.parent.parent
MOTOR = REPO / "tests" / "golden" / "MotorDemo" / "input.rbd"


# ---------------------------------------------------------------------------
# Minimal, dependency-free PNG reader (8-bit RGB/RGBA, non-interlaced —
# exactly what QPixmap.save("PNG") produces).
# ---------------------------------------------------------------------------

def _decode_png(path: Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    """Return (width, height, pixels) for a truecolour 8-bit PNG."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a PNG")
    pos = 8
    width = height = None
    colour_type = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, colour_type, _comp, _filt, interlace = \
                struct.unpack(">IIBBBBB", chunk)
            assert bit_depth == 8, "only 8-bit PNGs supported"
            assert interlace == 0, "interlaced PNGs not supported"
            assert colour_type in (2, 6), f"colour type {colour_type}"
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    assert width and height, "no IHDR"

    channels = 3 if colour_type == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    pixels: list[tuple[int, int, int]] = []
    prev = bytearray(stride)
    offset = 0
    for _row in range(height):
        ftype = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if ftype == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                line[i] = (line[i] + pred) & 0xFF
        elif ftype != 0:
            raise AssertionError(f"unknown PNG filter {ftype}")
        for x in range(width):
            base = x * channels
            pixels.append((line[base], line[base + 1], line[base + 2]))
        prev = line
    return width, height, pixels


def _distinct_colours(path: Path) -> int:
    _w, _h, pixels = _decode_png(path)
    return len(set(pixels))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestRenderMotorDemo:
    """The golden MotorDemo renders to a real, non-blank PNG."""

    def test_render_returns_png_and_metadata(self, tmp_path: Path):
        out = tmp_path / "motor.png"
        png_path, meta = render_diagram(MOTOR, out)

        assert png_path == str(out)
        assert out.is_file() and out.stat().st_size > 0
        assert meta["png_path"] == str(out)
        assert meta["width"] > 0 and meta["height"] > 0
        # MotorDemo has 5 blocks + Start/End nodes.
        assert meta["elements_drawn"] >= 5

    def test_render_is_not_blank(self, tmp_path: Path):
        """A blank/one-colour image must fail: headless render tests pass
        happily on white, so a distinct-colour check is required too."""
        out = tmp_path / "motor.png"
        render_diagram(MOTOR, out)
        assert _distinct_colours(out) > 1, "rendered PNG is a single colour"

    def test_render_within_size_cap(self, tmp_path: Path):
        out = tmp_path / "motor_small.png"
        _png, meta = render_diagram(MOTOR, out, max_px=400)
        assert max(meta["width"], meta["height"]) <= 400
        width, height, pixels = _decode_png(out)
        assert (width, height) == (meta["width"], meta["height"])
        assert len(set(pixels)) > 1

    def test_render_highlight_and_dark_theme(self, tmp_path: Path):
        out = tmp_path / "motor_hl.png"
        _png, meta = render_diagram(
            MOTOR, out, highlight=["Magnets", "Brushes"], theme="dark")
        assert meta["highlighted"] == 2
        assert meta["theme"] == "dark"
        assert _distinct_colours(out) > 1

    def test_render_highlight_by_id(self, tmp_path: Path):
        out = tmp_path / "motor_id.png"
        # Block id 3 is one of MotorDemo's blocks (ids start at 1).
        _png, meta = render_diagram(MOTOR, out, highlight=[3])
        assert meta["highlighted"] == 1

    def test_render_include_annotations_flag(self, tmp_path: Path):
        # MotorDemo is legacy and carries no annotations, so both modes
        # must still render cleanly (annotations_drawn == 0).
        out = tmp_path / "motor_noanno.png"
        _png, meta = render_diagram(MOTOR, out, include_annotations=False)
        assert meta["annotations_drawn"] == 0
        assert _distinct_colours(out) > 1

    def test_meta_has_model_hash_and_no_layout_flag(self, tmp_path: Path):
        """Rendering no longer re-lays-out, so there is no `auto_layout`
        result field; the model content hash is exposed instead."""
        out = tmp_path / "motor.png"
        _png, meta = render_diagram(MOTOR, out)
        assert "auto_layout" not in meta
        assert len(meta["model_hash"]) == 64

    def test_render_uses_stored_coordinates(self, tmp_path: Path):
        """The worker must render the model's authored x/y, never re-run
        layout.  Two files with identical topology but different stored
        geometry therefore produce different images."""
        moved = tmp_path / "moved.rbd"
        lines = []
        for line in MOTOR.read_text().splitlines(keepends=True):
            if line.startswith("Casing"):
                line = re.sub(r"(^Casing\s+\d+\s+[\d.]+\s+)([\d.]+)",
                              r"\g<1>40.000000", line)
            lines.append(line)
        moved.write_text("".join(lines))

        original = tmp_path / "original.png"
        shifted = tmp_path / "shifted.png"
        render_diagram(MOTOR, original, max_px=600)
        render_diagram(moved, shifted, max_px=600)
        assert original.read_bytes() != shifted.read_bytes(), (
            "stored coordinates were ignored -- the worker re-laid-out")


# ---------------------------------------------------------------------------
# The API no longer exposes a re-layout switch
# ---------------------------------------------------------------------------

class TestNoRelayoutParameter:
    """Layout is owned by the core auto_layout tool / build_model, so the
    render path must not accept (or send) an auto_layout option."""

    def test_render_diagram_signature_has_no_auto_layout(self):
        params = inspect.signature(render_diagram).parameters
        assert "auto_layout" not in params


# ---------------------------------------------------------------------------
# Failure modes -> typed errors
# ---------------------------------------------------------------------------

class TestRenderErrors:
    """Every failure is a typed RenderError, never a hang or traceback."""

    def test_qt_missing_is_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(render, "qt_available", lambda: False)
        with pytest.raises(RenderUnavailable):
            render_diagram(MOTOR, tmp_path / "x.png")

    def test_missing_model_is_render_failed(self, tmp_path):
        with pytest.raises(RenderFailed):
            render_diagram(tmp_path / "nope.rbd", tmp_path / "x.png")

    def test_timeout_is_typed(self, tmp_path):
        with pytest.raises(RenderTimeout):
            render_diagram(MOTOR, tmp_path / "x.png", timeout_s=0.05)

    def test_worker_success_without_output_is_typed(self, tmp_path,
                                                    monkeypatch):
        class _FakeProc:
            returncode = 0
            stdout = '{"width": 1, "height": 1}'
            stderr = ""

        monkeypatch.setattr(render.subprocess, "run",
                            lambda *a, **k: _FakeProc())
        with pytest.raises(RenderOutputMissing):
            render_diagram(MOTOR, tmp_path / "missing.png")

    def test_worker_nonzero_exit_is_typed(self, tmp_path, monkeypatch):
        class _FakeProc:
            returncode = 1
            stdout = ""
            stderr = "render worker error: boom\n"

        monkeypatch.setattr(render.subprocess, "run",
                            lambda *a, **k: _FakeProc())
        with pytest.raises(RenderFailed) as excinfo:
            render_diagram(MOTOR, tmp_path / "x.png")
        assert "boom" in str(excinfo.value)

    def test_bad_theme_is_render_error(self, tmp_path):
        with pytest.raises(RenderError):
            render_diagram(MOTOR, tmp_path / "x.png", theme="blue")

    def test_all_errors_share_base_class(self):
        for cls in (RenderUnavailable, RenderTimeout, RenderFailed,
                    RenderOutputMissing):
            assert issubclass(cls, RenderError)


# ---------------------------------------------------------------------------
# Worker CLI contract (tools_view.py drives it positionally)
# ---------------------------------------------------------------------------

class TestWorkerCli:
    """The worker also accepts positional args + flags, as the MCP layer
    invokes it."""

    def test_cli_positional_render(self, tmp_path: Path):
        import subprocess
        import sys

        out = tmp_path / "cli.png"
        proc = subprocess.run(
            [sys.executable, str(render.worker_path()),
             str(MOTOR), str(out),
             "--level", "0", "--theme", "light",
             "--max-px", "600"],
            capture_output=True, text=True, timeout=60, shell=False)
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()
        assert _distinct_colours(out) > 1

    def test_worker_bad_job_exits_nonzero_with_stderr(self, tmp_path: Path):
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, str(render.worker_path()),
             str(tmp_path / "missing.rbd"), str(tmp_path / "x.png")],
            capture_output=True, text=True, timeout=60, shell=False)
        assert proc.returncode != 0
        assert proc.stderr.strip()
        assert not (tmp_path / "x.png").exists()

"""Per-user file associations for .rbds and .rbd model files.

The installers register the two extensions themselves (`installer/windows.iss`,
the Linux package's `.desktop` entry and MIME XML). A pip or venv install has
no installer step, so `raptor register-file-types` does the same for the
current user only: opt-in, never at install time, and undone by
`--unregister`.

.rbd is Raptor 7's extension and other software may already open it. It is
taken only when nothing else owns it, or with `force`; .rbds is RAPTOR
Studio's own.

Linux follows the freedesktop rules under `$XDG_DATA_HOME` (default
`~/.local/share`): a shared-mime-info package, a desktop entry naming the
two types, and the icon. Windows writes `HKCU\\Software\\Classes`. Each
step is a `Step` so a dry run can list exactly what would change.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: (extension, MIME type, ProgID, description); .rbds first: it is ours.
TYPES = ((".rbds", "application/x-raptor-rbds", "RaptorStudio.rbds", "RAPTOR Studio model"),
         (".rbd", "application/x-raptor-rbd", "RaptorStudio.rbd", "Raptor 7 model"))

DESKTOP_ID = "raptor-studio.desktop"
MIME_FILE = "raptor-studio.xml"
ICON_NAME = "raptor-studio"


@dataclass
class Step:
    """One change: what it does, in words, and how to make it."""
    text: str
    run: Callable[[], None]


@dataclass
class Plan:
    steps: list[Step]
    #: Extensions left alone because another program opens them.
    skipped: list[str]
    notes: list[str]


def launcher() -> list[str]:
    """The command that opens a model in the window: the installed
    `raptor-studio` script when it is on PATH, else this Python with
    `-m raptor.gui.app`."""
    found = shutil.which("raptor-studio")
    if found:
        return [found]
    return [sys.executable, "-m", "raptor.gui.app"]


def _icon_source() -> Path:
    return Path(__file__).resolve().parent.parent / "gui" / "resources" / "raptor_icon.png"


def mime_xml() -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">']
    for ext, mime, _prog, comment in TYPES:
        lines += [f'  <mime-type type="{mime}">', f"    <comment>{comment}</comment>",
                  f'    <glob pattern="*{ext}"/>', f'    <icon name="{ICON_NAME}"/>',
                  "  </mime-type>"]
    lines.append("</mime-info>")
    return "\n".join(lines) + "\n"


def desktop_entry(command: list[str]) -> str:
    def quote(part: str) -> str:
        # the desktop-entry spec: a quoted argument escapes " ` $ and \
        if not any(c in part for c in ' \t"`$\\'):
            return part
        return '"' + "".join("\\" + c if c in '"`$\\' else c for c in part) + '"'
    exec_line = " ".join(quote(part) for part in command) + " %f"
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=Raptor Studio",
        "GenericName=Reliability Simulation",
        "Comment=Reliability block diagram simulation",
        f"Exec={exec_line}", f"Icon={ICON_NAME}", "Terminal=false",
        "Categories=Science;Engineering;",
        "MimeType=" + "".join(f"{mime};" for _e, mime, _p, _c in TYPES), ""])


# --- Linux -----------------------------------------------------------------------

def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _xdg_default(mime: str) -> str:
    """The desktop entry that opens `mime` now ("" when none or unknown)."""
    if not shutil.which("xdg-mime"):
        return ""
    try:
        out = subprocess.run(["xdg-mime", "query", "default", mime], capture_output=True,
                             text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _refresh_linux(data: Path) -> None:
    for tool, target in (("update-mime-database", data / "mime"),
                         ("update-desktop-database", data / "applications")):
        if shutil.which(tool):
            subprocess.run([tool, str(target)], capture_output=True, timeout=60, check=False)


def _unlink(path: Path) -> Callable[[], None]:
    return lambda: path.unlink()


def _call(*command: str) -> Callable[[], None]:
    def run() -> None:
        subprocess.run(list(command), capture_output=True, timeout=10, check=False)
    return run


def _write(path: Path, text: str) -> Callable[[], None]:
    def run() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return run


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _drop_defaults(mimeapps: Path) -> Callable[[], None]:
    """Remove the `xdg-mime default` lines that name this program."""
    def run() -> None:
        kept = []
        for line in mimeapps.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key in {m for _e, m, _p, _c in TYPES} and value.strip(";") == DESKTOP_ID:
                continue
            kept.append(line)
        mimeapps.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return run


def linux_plan(*, unregister: bool = False, force: bool = False,
               data_home: Path | None = None, config_home: Path | None = None,
               default_of: Callable[[str], str] = _xdg_default) -> Plan:
    data = data_home or _data_home()
    mime_path = data / "mime" / "packages" / MIME_FILE
    desktop_path = data / "applications" / DESKTOP_ID
    icon_path = data / "icons" / "hicolor" / "256x256" / "apps" / f"{ICON_NAME}.png"
    if unregister:
        return _linux_unregister(data, (mime_path, desktop_path, icon_path),
                                 (config_home or _config_home()) / "mimeapps.list")
    steps: list[Step] = []
    skipped: list[str] = []
    steps.append(Step(f"write {mime_path}", _write(mime_path, mime_xml())))
    steps.append(Step(f"write {desktop_path}", _write(desktop_path, desktop_entry(launcher()))))
    source = _icon_source()
    if source.is_file():
        def copy_icon() -> None:
            icon_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, icon_path)
        steps.append(Step(f"copy the icon to {icon_path}", copy_icon))
    steps.append(Step("refresh the MIME and desktop databases", lambda: _refresh_linux(data)))
    take = []
    for ext, mime, _prog, _comment in TYPES:
        owner = default_of(mime)
        if owner and owner != DESKTOP_ID and not force:
            skipped.append(f"{ext} (opened by {owner})")
        else:
            take.append(mime)
    if take and shutil.which("xdg-mime"):
        steps.append(Step("make RAPTOR Studio the default for " + ", ".join(take),
                          _call("xdg-mime", "default", DESKTOP_ID, *take)))
    return Plan(steps, skipped, [])


def _linux_unregister(data: Path, files: tuple[Path, ...], mimeapps: Path) -> Plan:
    steps = [Step(f"remove {path}", _unlink(path)) for path in files if path.exists()]
    if mimeapps.is_file() and DESKTOP_ID in mimeapps.read_text(encoding="utf-8"):
        steps.append(Step(f"remove the RAPTOR Studio defaults from {mimeapps}",
                          _drop_defaults(mimeapps)))
    if steps:
        steps.append(Step("refresh the MIME and desktop databases",
                          lambda: _refresh_linux(data)))
    return Plan(steps, [], [])


# --- Windows ---------------------------------------------------------------------

def _winreg() -> Any:
    """Windows' registry module (imported only there; Any for the type
    checker on other systems)."""
    import importlib
    return importlib.import_module("winreg")


class _Registry:
    """The few HKCU calls the Windows plan needs (replaced in tests)."""

    def get(self, key: str) -> str | None:
        winreg = _winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                return str(winreg.QueryValueEx(handle, "")[0])
        except OSError:
            return None

    def set(self, key: str, value: str) -> None:
        winreg = _winreg()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, value)

    def delete_tree(self, key: str) -> None:
        winreg = _winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                                winreg.KEY_ALL_ACCESS) as handle:
                while True:
                    try:
                        child = winreg.EnumKey(handle, 0)
                    except OSError:
                        break
                    self.delete_tree(f"{key}\\{child}")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass

    def notify(self) -> None:
        import ctypes
        # SHCNE_ASSOCCHANGED, SHCNF_IDLIST: Explorer re-reads associations
        shell: Any = ctypes        # windll exists only on Windows
        shell.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def _reg_delete(reg: _Registry, key: str) -> Callable[[], None]:
    return lambda: reg.delete_tree(key)


def _reg_set(reg: _Registry, key: str, value: str) -> Callable[[], None]:
    return lambda: reg.set(key, value)


def windows_plan(*, unregister: bool = False, force: bool = False,
                 registry: _Registry | None = None) -> Plan:
    reg = registry or _Registry()
    classes = "Software\\Classes"
    command = launcher()
    exe = command[0]
    open_cmd = " ".join(f'"{part}"' for part in command) + ' "%1"'
    steps: list[Step] = []
    skipped: list[str] = []
    for ext, _mime, prog, description in TYPES:
        current = reg.get(f"{classes}\\{ext}")
        if unregister:
            if current == prog:
                steps.append(Step(f"remove HKCU\\{classes}\\{ext}",
                                  _reg_delete(reg, f"{classes}\\{ext}")))
            steps.append(Step(f"remove HKCU\\{classes}\\{prog}",
                              _reg_delete(reg, f"{classes}\\{prog}")))
            continue

        def register(prog: str = prog, description: str = description) -> None:
            reg.set(f"{classes}\\{prog}", description)
            reg.set(f"{classes}\\{prog}\\DefaultIcon", f"{exe},0")
            reg.set(f"{classes}\\{prog}\\shell\\open\\command", open_cmd)
        steps.append(Step(f"write HKCU\\{classes}\\{prog} (open with {open_cmd})", register))
        if current and current != prog and not force:
            skipped.append(f"{ext} (opened by {current})")
            continue
        steps.append(Step(f"point {ext} at {prog}",
                          _reg_set(reg, f"{classes}\\{ext}", prog)))
    if steps:
        steps.append(Step("tell Explorer the associations changed", reg.notify))
    return Plan(steps, skipped, [])


def plan(*, unregister: bool = False, force: bool = False) -> Plan:
    if sys.platform.startswith("win"):
        return windows_plan(unregister=unregister, force=force)
    if sys.platform == "darwin":
        return Plan([], [], ["macOS associates files through the app bundle "
                             "(CFBundleDocumentTypes); there is nothing to register "
                             "for a pip install."])
    return linux_plan(unregister=unregister, force=force)

"""`raptor register-file-types`: per-user .rbds/.rbd associations, opt-in,
reversible, and never taking .rbd from another program unless forced."""
from __future__ import annotations

import time

from raptor.api import file_types as ft


def _apply(plan):
    for step in plan.steps:
        step.run()


def test_linux_registers_and_unregisters_in_the_users_data(tmp_path, monkeypatch):
    monkeypatch.setattr(ft.shutil, "which", lambda name: None)   # no xdg tools
    data, config = tmp_path / "data", tmp_path / "config"
    plan = ft.linux_plan(data_home=data, default_of=lambda mime: "")
    _apply(plan)
    mime = (data / "mime" / "packages" / ft.MIME_FILE).read_text()
    assert '<glob pattern="*.rbds"/>' in mime and '<glob pattern="*.rbd"/>' in mime
    desktop = (data / "applications" / ft.DESKTOP_ID).read_text()
    assert "MimeType=application/x-raptor-rbds;application/x-raptor-rbd;" in desktop
    assert desktop.splitlines()[5].endswith(" %f")
    # a default left by `xdg-mime default` is removed with the rest
    config.mkdir()
    (config / "mimeapps.list").write_text(
        "[Default Applications]\napplication/x-raptor-rbds=raptor-studio.desktop\n"
        "text/plain=gedit.desktop\n")
    _apply(ft.linux_plan(unregister=True, data_home=data, config_home=config))
    assert not (data / "applications" / ft.DESKTOP_ID).exists()
    assert not (data / "mime" / "packages" / ft.MIME_FILE).exists()
    left = (config / "mimeapps.list").read_text()
    assert "raptor-studio" not in left and "text/plain=gedit.desktop" in left


def test_linux_leaves_another_programs_rbd_alone_unless_forced(tmp_path):
    owners = {"application/x-raptor-rbd": "other-tool.desktop"}
    plan = ft.linux_plan(data_home=tmp_path, default_of=lambda m: owners.get(m, ""))
    assert plan.skipped == [".rbd (opened by other-tool.desktop)"]
    forced = ft.linux_plan(data_home=tmp_path, force=True,
                           default_of=lambda m: owners.get(m, ""))
    assert forced.skipped == []


def test_the_desktop_entry_quotes_a_path_with_spaces():
    line = next(x for x in ft.desktop_entry(["/opt/my tools/python", "-m", "raptor.gui.app"])
                .splitlines() if x.startswith("Exec="))
    assert line == 'Exec="/opt/my tools/python" -m raptor.gui.app %f'


class FakeRegistry:
    def __init__(self, existing=None):
        self.keys = dict(existing or {})
        self.notified = False

    def get(self, key):
        return self.keys.get(key)

    def set(self, key, value):
        self.keys[key] = value

    def delete_tree(self, key):
        for name in [k for k in self.keys if k == key or k.startswith(key + "\\")]:
            del self.keys[name]

    def notify(self):
        self.notified = True


def test_windows_writes_hkcu_and_keeps_a_foreign_rbd():
    reg = FakeRegistry({"Software\\Classes\\.rbd": "OtherApp.Model"})
    plan = ft.windows_plan(registry=reg)
    _apply(plan)
    assert reg.keys["Software\\Classes\\.rbds"] == "RaptorStudio.rbds"
    assert reg.keys["Software\\Classes\\.rbd"] == "OtherApp.Model"       # left alone
    assert reg.keys["Software\\Classes\\RaptorStudio.rbds\\shell\\open\\command"].endswith('"%1"')
    assert plan.skipped == [".rbd (opened by OtherApp.Model)"] and reg.notified
    _apply(ft.windows_plan(unregister=True, registry=reg))
    assert reg.keys == {"Software\\Classes\\.rbd": "OtherApp.Model"}


def test_the_command_is_listed_and_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    from raptor.cli.main import COMMANDS, main
    assert "register-file-types" in COMMANDS
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(ft.sys, "platform", "linux")
    monkeypatch.setattr(ft, "_xdg_default", lambda mime: "")
    assert main(["register-file-types", "--dry-run"]) == 0
    assert "would write" in capsys.readouterr().out
    assert not any(tmp_path.iterdir())


def test_run_status_gives_an_eta_from_the_rate_after_the_first_run():
    from raptor.mcp.async_runs import Job
    job = Job(job_id="j", model_id="m", total=100, state="running")
    assert job.snapshot()["eta_s"] is None
    job.done = 10
    job.first_run = (time.time() - 9.0, 1)          # 9 runs in 9 s after the first
    eta = job.snapshot()["eta_s"]
    assert 85 <= eta <= 95

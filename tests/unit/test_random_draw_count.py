"""The Final Results `Random draws: N` line (counter),
against a real capture of `Phasing3` launched with the master switch (10 runs x 100 cycles)."""
from __future__ import annotations

from pathlib import Path

from raptor.core.engine.runner import run_simulation_with_reports
from raptor.core.fileio.rbd import load_rbd

GOLD = Path(__file__).resolve().parents[1] / "golden" / "Phasing3"


def _final(**kw):
    meta: dict = {}
    _, _, fin = run_simulation_with_reports(
        load_rbd(str(GOLD / "input.rbd")), date="DATE", metadata_sink=meta, **kw)
    return fin, meta


def test_final_results_matches_real_with_the_line():
    real = (GOLD / "legacy" / "rpt_final_results_random_draws_Phasing3.txt"
            ).read_bytes().decode("latin-1").replace("\r", "").split("\n")
    fin, meta = _final(count_draws=True)
    assert meta["random_draws"] == 13308
    # Header carries the capture's date; the body is exact.
    assert fin[1:] == real[1:len(fin)]
    assert real[len(fin):] in ([], [""])


def test_off_leaves_the_report_alone():
    fin, meta = _final()
    assert "random_draws" not in meta
    assert not any(l.startswith("Random draws") for l in fin)
    plain = (GOLD / "legacy" / "rpt_final_results_phasing3.txt"
             ).read_bytes().decode("latin-1").replace("\r", "").split("\n")
    assert fin[1:] == plain[1:len(fin)]


def test_count_is_the_number_of_draw_lines_in_the_sd_file(tmp_path):
    sd = tmp_path / "sd.txt"
    _, meta = _final(count_draws=True, sim_details_path=str(sd))
    assert meta["random_draws"] == len(sd.read_bytes().splitlines())


def test_parallel_request_still_counts_the_whole_batch():
    from raptor.core.engine.runner import run_simulation
    meta: dict = {}
    run_simulation(load_rbd(str(GOLD / "input.rbd")), 4, num_workers=2, count_draws=True,
                   metadata_sink=meta)
    assert meta["random_draws"] > 0


def test_api_run_honours_the_option(tmp_path, monkeypatch):
    """Reachability: the option changes `api.run`'s Final Results (env and argument)."""
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    from raptor.core.dev_options import DeveloperOptions

    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "input.rbd")["model_id"]

    def final(**kw):
        res = run(ws, mid, num_runs=2, timeout_s=60, confirm_long_run=True, **kw)
        return section_path(ws.root / "runs" / res.run_id, "final_results").read_text()

    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    assert "Random draws" not in final()
    assert "Random draws:" in final(developer=DeveloperOptions(random_draw_count=True))
    monkeypatch.setenv("RAPTOR_DEV_FEATURES", "random_draw_count")
    assert "Random draws:" in final()


def test_sim_details_option_writes_the_draw_log(tmp_path, monkeypatch):
    from raptor.api.run import run, section_path
    from raptor.api.workspace import Workspace
    from raptor.core.dev_options import DeveloperOptions

    ws = Workspace(tmp_path)
    mid = ws.open_model(GOLD / "input.rbd")["model_id"]
    monkeypatch.delenv("RAPTOR_DEV_FEATURES", raising=False)
    off = run(ws, mid, num_runs=1, timeout_s=60, confirm_long_run=True)
    assert not section_path(ws.root / "runs" / off.run_id, "sim_details").exists()
    on = run(ws, mid, num_runs=1, timeout_s=60, confirm_long_run=True,
             developer=DeveloperOptions(sim_details=True))
    text = section_path(ws.root / "runs" / on.run_id, "sim_details").read_bytes()
    assert text.endswith(b"\r\n") and len(text.splitlines()) > 100


def test_sim_details_file_appends_across_launches(tmp_path):
    p = tmp_path / "simDetails.TXT"
    for _ in range(2):
        run_simulation_with_reports(load_rbd(str(GOLD / "input.rbd")), 1, date="D",
                                    sim_details_path=str(p), sim_details_append=True)
    lines = p.read_bytes().splitlines()
    half = len(lines) // 2
    assert len(lines) % 2 == 0 and lines[:half] == lines[half:]

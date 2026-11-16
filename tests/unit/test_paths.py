

def test_a_spare_pool_stock_and_a_crew_count_are_paths(tmp_path):
    from raptor.api.workspace import Workspace
    from raptor.core.fileio.rbd import load_rbd

    ws = Workspace(tmp_path)
    mid = ws.register_model(
        load_rbd("tests/golden/SpaceStation/input.rbd"),
        name="station")["model_id"]
    from raptor.api.paths import apply_assignment, get_value
    apply_assignment(ws, mid, "pool:Cells.stock=7")
    assert get_value(ws, mid, "pool:Cells.stock") == 7
    apply_assignment(ws, mid, "resource:Astronauts.units=6")
    assert get_value(ws, mid, "resource:Astronauts.units") == 6

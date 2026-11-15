"""Each model rule: a model where it fires and one just on the silent side."""
from __future__ import annotations

import pytest

from raptor.api import build
from raptor.api.advisor import advise_model, fixes
from raptor.api.advisor.rules_model import spares as spares_rules
from raptor.api.advisor.stats import poisson_cdf, smallest_stock
from raptor.api.workspace import Workspace

from .helpers import RANDOM, WEAROUT, block, findings, parallel_spec, rules_fired, series_spec


def one(spec, rule):
    hits = findings(spec, rule)
    assert len(hits) == 1, [f.message for f in hits]
    return hits[0]


def apply_first_fix(tmp_path, spec, rule):
    """Apply the rule's first fix through the public path API; return the findings after."""
    ws = Workspace(tmp_path)
    info = ws.register_model(build.build_from_spec(spec), name="m")
    finding = one(spec, rule)
    fixes.apply(ws, info["model_id"], finding.fixes[0])
    return advise_model(ws.get(info["model_id"]).rbd)


# ---- structure ---------------------------------------------------------------

def _backup(dep_idle=100.0):
    return parallel_spec(block("A"), block("B", dep_type="RBDBlock", dep_num=1,
                                           dep_idle=dep_idle, dep_nothing=100.0 - dep_idle))


def test_M_STR_002_fires(tmp_path):
    f = one(_backup(), "M-STR-002")
    assert "B backs up A" in f.message and "idle" in f.message
    assert f.targets[0].name == "B" and f.basis == "exact"
    after = apply_first_fix(tmp_path, _backup(), "M-STR-002")
    assert "M-STR-002" not in {x.rule for x in after}
    # the fixed model must still validate and run (the fix once left
    # dep_num set, a dependency of type None that validation rejects)
    from raptor.api.run import run
    from raptor.api.validate import issues
    ws = Workspace(tmp_path)
    model_id = ws.list_models()[0]["model_id"]
    errors = [i for i in issues(ws.get(model_id).rbd) if i.severity == "error"]
    assert not errors, errors
    run(ws, model_id, num_runs=2)


def test_M_STR_002_silent():
    assert "M-STR-002" not in rules_fired(parallel_spec(block("A"), block("B")))
    # a dependency that never reacts defeats nothing
    assert "M-STR-002" not in rules_fired(_backup(dep_idle=0.0))
    # depending on a block outside the redundant group is a different situation
    spec = parallel_spec(block("A"), block("B"))
    spec["blocks"].append(block("C", dep_type="RBDBlock", dep_num=1, dep_idle=100))
    spec["links"] = [link for link in spec["links"] if link != {"from": "Join", "to": "End"}]
    spec["links"] += [{"from": "Join", "to": "C"}, {"from": "C", "to": "End"}]
    assert "M-STR-002" not in rules_fired(spec)


def test_M_STR_003_fires(tmp_path):
    spec = parallel_spec(block("A"), block("B"), block("C"), k=3)
    f = one(spec, "M-STR-003")
    assert f.evidence == {"k": 3, "n": 3} and "all 3 of its 3" in f.message
    after = apply_first_fix(tmp_path, spec, "M-STR-003")
    assert "M-STR-003" not in {x.rule for x in after}


def test_M_STR_003_silent():
    assert "M-STR-003" not in rules_fired(parallel_spec(block("A"), block("B"), block("C"), k=2))
    assert "M-STR-003" not in rules_fired(series_spec(block("A"), block("B")))


# ---- data ----------------------------------------------------------------------

def test_M_DAT_001_fires_once_for_all_default_blocks():
    f = one(series_spec({"name": "A"}, {"name": "B"}, block("C")), "M-DAT-001")
    assert f.evidence == {"blocks": 2, "of": 3}
    assert [t.name for t in f.targets] == ["A", "B"]
    assert f.impact == pytest.approx(2 / 3)


def test_M_DAT_001_silent():
    assert "M-DAT-001" not in rules_fired(series_spec(block("A"), block("B")))
    # changing only the repair data is enough to count as entered
    assert "M-DAT-001" not in rules_fired(series_spec({"name": "A", "repair_params": [11, 2, 0]}))


def test_M_DAT_002_fires():
    spec = series_spec(block("A", fail_params=[10, 0, 0], repair_params=[40, 5, 0]))
    f = one(spec, "M-DAT-002")
    assert f.evidence["availability"] == pytest.approx(0.2)


def test_M_DAT_002_silent():
    spec = series_spec(block("A", fail_params=[41, 0, 0], repair_params=[40, 5, 0]))
    assert "M-DAT-002" not in rules_fired(spec)


def test_M_DAT_006_fires(tmp_path):
    spec = series_spec(block("A", fail_dist="Weibull", fail_params=[0.7, 4000, 0],
                             uses_pm=True, pm_freq=500))
    f = one(spec, "M-DAT-006")
    assert f.evidence["weibull_shape"] == pytest.approx(0.7)
    assert "M-DAT-007" not in rules_fired(spec)
    after = apply_first_fix(tmp_path, spec, "M-DAT-006")
    assert "M-DAT-006" not in {x.rule for x in after}


def test_M_DAT_006_silent():
    assert "M-DAT-006" not in rules_fired(series_spec(block("A", WEAROUT, uses_pm=True, pm_freq=500)))
    assert "M-DAT-006" not in rules_fired(
        series_spec(block("A", fail_dist="Weibull", fail_params=[0.7, 4000, 0])))


def test_M_DAT_007_fires():
    spec = series_spec(block("A", uses_pm=True, pm_freq=876, pm_params=[2, 0, 0]))
    f = one(spec, "M-DAT-007")
    assert f.evidence["pm_time_per_run"] == pytest.approx(20.0)     # 8760/876 x 2
    weibull_one = series_spec(block("A", fail_dist="Weibull", fail_params=[1.0, 4000, 0],
                                    uses_pm=True, pm_freq=500))
    assert "M-DAT-007" in rules_fired(weibull_one)


def test_M_DAT_007_silent():
    assert "M-DAT-007" not in rules_fired(series_spec(block("A", WEAROUT, uses_pm=True, pm_freq=500)))
    assert "M-DAT-007" not in rules_fired(series_spec(block("A")))


# ---- spares --------------------------------------------------------------------

def test_poisson_helpers():
    assert poisson_cdf(0, 2.0) == pytest.approx(0.1353352832)
    assert poisson_cdf(3, 2.0) == pytest.approx(0.8571234605)
    assert poisson_cdf(-1, 2.0) == 0.0 and poisson_cdf(5, 0.0) == 1.0
    assert smallest_stock(2.0, 0.05) == 5            # P(X>4)=0.0527, P(X>5)=0.0166


def test_M_SPR_001_fires_and_is_silent():
    assert "M-SPR-001" in rules_fired(series_spec(block("A"), block("B")))
    assert "M-SPR-001" not in rules_fired(
        series_spec(block("A"), block("B", sparing_type="Custom", stock=50, rso=0)))


def _own_stock(stock, **more):
    # 8760 h run, mean life 876 h -> about 10 demands
    fields = {"fail_params": [876, 0, 0], "sparing_type": "Custom", "stock": stock,
              "rso": 0, "eso": 0, **more}
    return series_spec(block("A", **fields))


def test_M_SPR_002_fires(tmp_path):
    f = one(_own_stock(4), "M-SPR-002")
    assert f.evidence["expected_demand"] == pytest.approx(10.0)
    assert f.evidence["supply"] == 4 and f.evidence["suggested_stock"] == 10
    assert f.basis == "heuristic"
    after = {x.rule for x in apply_first_fix(tmp_path, _own_stock(4), "M-SPR-002")}
    assert "M-SPR-002" not in after


def test_M_SPR_002_counts_routine_deliveries():
    # 4 on the shelf + 1 every 1,000 h (8 deliveries) = 12 >= 10
    assert "M-SPR-002" not in rules_fired(_own_stock(4, rso=1, new_spares=1, arrive_every=1000))
    f = one(_own_stock(1, rso=1, new_spares=1, arrive_every=1000), "M-SPR-002")
    assert f.evidence["supply"] == 9 and f.evidence["deliveries"] == 8


def test_M_SPR_002_is_silent_when_shortages_are_reordered():
    """Stock-level or emergency ordering sends an order for every shortage,
    so "only 4 can arrive" is false: FactoryDemo (availability 100 %) got
    five such warnings and the fix "Set stock to 12". The lead-time risk is
    M-SPR-003's, judged against the reorder level, and it suggests a level,
    never a stock."""
    reorder = _own_stock(4, slo=1, level=1, quantity=2, slo_time=72)
    assert "M-SPR-002" not in rules_fired(reorder)
    assert "M-SPR-002" not in rules_fired(_own_stock(4, eso=1, emer_time=24))
    risky = one(_own_stock(4, slo=1, level=0, quantity=2, slo_time=500), "M-SPR-003")
    assert risky.evidence["p_stockout"] == pytest.approx(1 - poisson_cdf(0, 500 / 876))
    assert risky.fixes == ()
    assert "M-SPR-003" not in rules_fired(
        _own_stock(4, slo=1, level=6, quantity=2, slo_time=72))


def test_M_SPR_002_silent():
    assert "M-SPR-002" not in rules_fired(_own_stock(10))


def test_M_SPR_003_fires():
    f = one(_own_stock(12), "M-SPR-003")       # covers the mean, not the bad years
    assert f.evidence["p_stockout"] == pytest.approx(1 - poisson_cdf(12, 10.0))
    assert f.evidence["suggested_stock"] == smallest_stock(10.0, spares_rules.STOCKOUT_P)
    assert "M-SPR-002" not in rules_fired(_own_stock(12))


def test_M_SPR_003_silent():
    enough = smallest_stock(10.0, spares_rules.STOCKOUT_P)
    assert "M-SPR-003" not in rules_fired(_own_stock(enough))
    assert "M-SPR-003" in rules_fired(_own_stock(enough - 1))


def test_pool_demand_adds_its_blocks():
    spec = series_spec(
        block("A", fail_params=[876, 0, 0], sparing_type="Pooled", spare_pool="Seals"),
        block("B", fail_params=[876, 0, 0], sparing_type="Pooled", spare_pool="Seals"),
        spare_pools=[{"name": "Seals", "stock": 5}])
    f = one(spec, "M-SPR-002")
    assert f.evidence["expected_demand"] == pytest.approx(20.0)
    assert f.targets[0].path() == "pool:Seals.stock" and f.fixes == ()


def test_M_SPR_006_fires_and_is_silent():
    slow = _own_stock(50, rso=1, new_spares=5, arrive_every=100, eso=1, emer_time=100)
    assert one(slow, "M-SPR-006").evidence == {"emer_time": 100.0, "arrive_every": 100.0}
    fast = _own_stock(50, rso=1, new_spares=5, arrive_every=100, eso=1, emer_time=24)
    assert "M-SPR-006" not in rules_fired(fast)


def test_M_SPR_007_fires_and_is_silent():
    assert one(_own_stock(50, slo=1, level=50), "M-SPR-007").evidence == {"level": 50, "stock": 50}
    assert "M-SPR-007" not in rules_fired(_own_stock(50, slo=1, level=49))
    assert "M-SPR-007" not in rules_fired(_own_stock(50, slo=0, level=50))


def test_M_SPR_008_fires_and_is_silent():
    unused = series_spec(block("A"), spare_pools=[{"name": "Seals", "stock": 5}])
    assert "Seals" in one(unused, "M-SPR-008").message
    used = series_spec(block("A", sparing_type="Pooled", spare_pool="Seals"),
                       spare_pools=[{"name": "Seals", "stock": 500}])
    assert "M-SPR-008" not in rules_fired(used)


# ---- resources -----------------------------------------------------------------

def _crewed(n_blocks, crew=1, needed=1, life=100.0, repair=10.0):
    blocks = [block(f"B{i}", fail_params=[life, 0, 0], repair_params=[repair, 1, 0],
                    num_resources=1, resource_name="Crew", repair_resources_needed=needed)
              for i in range(n_blocks)]
    return series_spec(*blocks, resources=[{"name": "Crew", "num_in": crew}])


def test_M_RES_001_fires_and_is_silent():
    f = one(_crewed(1, crew=1, needed=2), "M-RES-001")
    assert f.severity == "error" and f.evidence == {"needed": 2, "available": 1}
    assert "M-RES-001" not in rules_fired(_crewed(1, crew=2, needed=2))


def test_M_RES_002_overloaded():
    f = one(_crewed(12), "M-RES-002")                 # 12 x 10/100 = 1.2 crews of work
    assert f.evidence["load"] == pytest.approx(1.2)
    assert f.evidence["suggested_units"] == 2
    assert "M-RES-003" not in rules_fired(_crewed(12))


def test_M_RES_003_busy_and_silent_below():
    f = one(_crewed(8), "M-RES-003")                  # 0.8
    assert f.severity == "advice" and f.evidence["load"] == pytest.approx(0.8)
    fired = rules_fired(_crewed(6))                   # 0.6
    assert "M-RES-003" not in fired and "M-RES-002" not in fired


def test_M_RES_005_fires_and_is_silent():
    assert "M-RES-005" in rules_fired(series_spec(block("A"), resources=[{"name": "Crew", "num_in": 1}]))
    assert "M-RES-005" not in rules_fired(_crewed(1))


# ---- preventive maintenance ----------------------------------------------------

def test_M_PM_001_fires_and_is_silent():
    f = one(series_spec(block("A", WEAROUT, uses_pm=True, pm_freq=10000)), "M-PM-001")
    assert f.evidence == {"first_pm": 10000.0, "run_length": 8760.0}
    staggered = series_spec(block("A", WEAROUT, uses_pm=True, pm_freq=10000, pm_stagger_time=2000))
    assert "M-PM-001" not in rules_fired(staggered)
    assert "M-PM-001" not in rules_fired(series_spec(block("A", WEAROUT)))


def _pm_pair(stagger_b=0.0, k=1):
    return parallel_spec(block("A", WEAROUT, uses_pm=True, pm_freq=1000),
                         block("B", WEAROUT, uses_pm=True, pm_freq=1000, pm_stagger_time=stagger_b),
                         k=k)


def test_M_PM_003_fires(tmp_path):
    f = one(_pm_pair(), "M-PM-003")
    assert "A and B" in f.message and "goes down during maintenance" in f.message
    assert f.fixes[0].assignments == ("block:2.pm_stagger_time=500",)
    after = {x.rule for x in apply_first_fix(tmp_path, _pm_pair(), "M-PM-003")}
    assert "M-PM-003" not in after


def test_M_PM_003_silent():
    assert "M-PM-003" not in rules_fired(_pm_pair(stagger_b=500))
    assert "M-PM-003" not in rules_fired(_pm_pair(k=2))        # no redundancy to lose
    series = series_spec(block("A", WEAROUT, uses_pm=True, pm_freq=1000),
                         block("B", WEAROUT, uses_pm=True, pm_freq=1000))
    assert "M-PM-003" not in rules_fired(series)


def test_M_PM_004_fires_and_is_silent():
    life = build_mean = 4000 * 0.8872638175            # Weibull(2.5, 4000) mean
    assert build_mean > 0
    late = series_spec(block("A", WEAROUT, uses_pm=True, pm_freq=3.1 * life,
                             pm_stagger_time=3.1 * life - 100))
    assert one(late, "M-PM-004").evidence["mean_life"] == pytest.approx(life, rel=1e-6)
    soon = series_spec(block("A", WEAROUT, uses_pm=True, pm_freq=2.9 * life))
    assert "M-PM-004" not in rules_fired(soon)
    random_block = series_spec(block("A", RANDOM, uses_pm=True, pm_freq=50000,
                                     pm_stagger_time=49000))
    assert "M-PM-004" not in rules_fired(random_block)


# ---- set-up --------------------------------------------------------------------

def _runs(n):
    return series_spec(block("A"), experiment={"trials": n, "termination_type": "TimeSim",
                                               "term_condition": 8760})


def test_M_EXP_001_and_002(tmp_path):
    assert one(_runs(9), "M-EXP-001").severity == "warning"
    assert one(_runs(10), "M-EXP-002").severity == "advice"
    assert one(_runs(29), "M-EXP-002").evidence == {"runs": 29}
    fired = rules_fired(_runs(30))
    assert "M-EXP-001" not in fired and "M-EXP-002" not in fired
    after = {x.rule for x in apply_first_fix(tmp_path, _runs(3), "M-EXP-001")}
    assert not after & {"M-EXP-001", "M-EXP-002"}


def test_M_CAP_001_fires_and_is_silent():
    exp = {"trials": 200, "termination_type": "TimeSim", "term_condition": 8760,
           "analyses": ["capacity"]}
    no_flow = series_spec(block("A"), experiment=exp, system={"flow_generated": 0})
    assert "M-CAP-001" in rules_fired(no_flow)
    flow = series_spec(block("A"), experiment=exp, system={"flow_generated": 100})
    assert "M-CAP-001" not in rules_fired(flow)
    off = series_spec(block("A"), system={"flow_generated": 0})
    assert "M-CAP-001" not in rules_fired(off)


# ---- the whole catalogue -------------------------------------------------------

def test_a_sound_model_is_quiet():
    spec = parallel_spec(block("A", WEAROUT, uses_pm=True, pm_freq=2000),
                         block("B", WEAROUT, uses_pm=True, pm_freq=2000, pm_stagger_time=1000))
    assert rules_fired(spec) == {"M-SPR-001"}


def test_validation_issues_are_carried_over():
    spec = series_spec(block("A", fail_dist="Weibull", fail_params=[-1, 100, 0]))
    try:
        rbd = build.build_from_spec(spec)
    except Exception:  # noqa: BLE001 - the builder may refuse the bad value outright
        pytest.skip("builder refuses the invalid parameter before validation")
    hits = [f for f in advise_model(rbd) if f.rule == "M-VAL-000"]
    assert hits and hits[0].severity == "error" and hits[0].targets[0].name == "A"


def test_blocks_that_share_a_name_are_told_apart():
    """G16: two unnamed blocks gave two identical Advisor lines."""
    from pathlib import Path

    from raptor.api.advisor import advise_model
    from raptor.api.advisor.context import _distinct_names
    from raptor.core.fileio.rbd import load_rbd

    rows = _distinct_names([{"id": 3, "name": None}, {"id": 4, "name": "Pump"},
                            {"id": 5, "name": "Pump"}, {"id": 6, "name": "Valve"}], "block")
    assert [r["name"] for r in rows] == ["block 3", "Pump (block 4)", "Pump (block 5)", "Valve"]
    gold = Path(__file__).resolve().parents[2] / "golden" / "TwoBlockRedundancy" / "input.rbd"
    messages = [f.message for f in advise_model(load_rbd(str(gold)))]
    assert len(messages) == len(set(messages))


def test_unlimited_spares_are_not_noted_when_no_block_is_repaired():
    """U29: Lantern1's blocks are never repaired, so they draw no spare."""
    from pathlib import Path

    from raptor.api.advisor import advise_model as advise
    from raptor.core.fileio.model_file import load_model_file

    models = Path(__file__).resolve().parents[3] / "new_docs" / "models"
    rules = {f.rule for f in advise(load_model_file(models / "Lantern1.rbds"))}
    assert "M-SPR-001" not in rules
    assert "M-SPR-001" in {f.rule for f in advise(load_model_file(models / "Van1.rbds"))}

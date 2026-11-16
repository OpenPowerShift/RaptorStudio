"""Validation warns when PM needs a spare that no stock can supply."""
from __future__ import annotations

from raptor.api.validate import issues
from raptor.core.fileio.rbd import load_rbd


def _block(rbd):
    return next(r for r in rbd.sections["BLOCK_DATA"].rows
                if r.get("ID") not in ("0", None))


def _hits(rbd):
    return [i for i in issues(rbd) if "needs a spare" in i.message]


def _setup(**cols):
    rbd = load_rbd("tests/golden/Phasing3/input.rbd")
    _block(rbd).update(usesPM="1", PMSpare="1", **cols)
    return rbd


def test_empty_custom_supply_warns_with_emergency_wording():
    found = _hits(_setup(SpType="Custom", Stock="0", NewSp="0", ESO="1"))
    assert len(found) == 1
    assert found[0].severity == "warning"
    assert "emergency order" in found[0].message


def test_empty_custom_supply_without_eso_says_skipped_or_held():
    found = _hits(_setup(SpType="Custom", Stock="0", NewSp="0", ESO="0"))
    assert len(found) == 1 and "skipped or held" in found[0].message


def test_stock_or_new_spares_silence_it():
    assert _hits(_setup(SpType="Custom", Stock="1", NewSp="0")) == []
    assert _hits(_setup(SpType="Custom", Stock="0", NewSp="2")) == []


def test_infinite_or_pm_not_needing_spare_is_silent():
    assert _hits(_setup(SpType="Infinite", Stock="0", NewSp="0")) == []
    rbd = _setup(SpType="Custom", Stock="0", NewSp="0")
    _block(rbd)["PMSpare"] = "0"
    assert _hits(rbd) == []


def test_empty_pool_warns():
    rbd = _setup(SpType="Pooled", SparePoolName="P")
    from raptor.core.fileio.rbdlib import SPARE_COLS
    row = dict.fromkeys(SPARE_COLS, "0")
    row["PoolName"] = "P"
    rbd.sections["SPARE_POOL_DATA"].rows.append(row)
    found = _hits(rbd)
    assert len(found) == 1 and "spare pool 'P'" in found[0].message

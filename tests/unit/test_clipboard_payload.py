"""The clipboard payload: encode/decode and what a foreign model keeps."""
from __future__ import annotations

import json

from raptor.core.fileio.rbd import load_rbd
from raptor.gui import clipboard_payload as cp

MODEL = "tests/golden/Phasing3/input.rbd"


def _block(rbd, name="Attitude"):
    return dict(next(r for r in rbd.sections["BLOCK_DATA"].rows
                     if r.get("blockName") == name))


def test_round_trip_keeps_rows_in_order():
    buf = [("block", {"ID": "1", "blockName": "A"}),
           ("node", {"ID": "2", "Name": "N"}),
           ("link", {"ID": "1", "FromType": "Block", "FromID": "1",
                     "ToType": "Node", "ToID": "2"}),
           ("annotation", {"id": 3, "kind": "text", "x": 1.5})]
    got = cp.decode(cp.encode(buf, source_name="m.rbd", model_token="t"))
    assert got is not None and got.buffer == buf
    assert got.source_name == "m.rbd" and got.process == cp.PROCESS_TOKEN


def test_garbage_and_foreign_payloads_are_ignored():
    for bad in (None, b"", b"not json", b"[1,2]", b'{"format":"x"}',
                b'{"format":"raptor-studio-clip","version":2,"items":[]}',
                b'{"format":"raptor-studio-clip","version":1,"items":[]}',
                b'{"format":"raptor-studio-clip","version":1,'
                b'"items":[["spaceship",{}]]}',
                b'{"format":"raptor-studio-clip","version":1,'
                b'"items":[["block",{"ID":7}]]}',
                b"\xff\xfe\x00", b"[" * 100000):
        assert cp.decode(bad) is None


def test_summary_text_is_readable():
    text = cp.summary_text([("block", {}), ("block", {}), ("hier", {}),
                            ("link", {})], "m.rbd")
    assert text == "Raptor Studio: 2 blocks, 1 hierarchy, 1 link from m.rbd"


def test_same_model_paste_is_unchanged():
    rbd = load_rbd(MODEL)
    row = _block(rbd)
    row["SpType"], row["SparePoolName"] = "Pooled", "NoSuchPool"
    p = cp.Payload([("block", row)], "x", "tok", cp.PROCESS_TOKEN)
    assert cp.same_model(p, "tok") and not cp.same_model(p, "other")
    out = cp.adapt_for_target(rbd, p, same_model=True)
    assert out.buffer == [("block", row)] and out.notes == []


def test_missing_definitions_are_dropped_with_a_note():
    rbd = load_rbd(MODEL)
    row = _block(rbd)
    row.update({"SpType": "Pooled", "SparePoolName": "Foreign_Pool",
                "NumRes": "1", "Resource1Name": "Foreign_Crew",
                "pmTriggered": "1", "pmTrig": "Foreign_Trig",
                "P 001": "A"})
    p = cp.Payload([("block", row)], "x", "tok", "other-process")
    out = cp.adapt_for_target(rbd, p, same_model=False)
    (_, got), = out.buffer
    tpl = next(r for r in rbd.sections["BLOCK_DATA"].rows if r["ID"] == "0")
    assert got["SpType"] == tpl["SpType"]
    assert got["SparePoolName"] == tpl["SparePoolName"]
    assert got["NumRes"] == tpl["NumRes"]
    assert got["Resource1Name"] == tpl["Resource1Name"]
    assert got["pmTriggered"] == tpl["pmTriggered"]
    assert "P 001" not in got                 # the target has no phases
    assert set(got) == set(tpl)
    text = " ".join(out.notes)
    assert "Foreign_Pool" in text and "Foreign_Crew" in text
    assert "Foreign_Trig" in text


def test_annotation_anchors_are_cleared_across_models():
    rbd = load_rbd(MODEL)
    a = {"id": 1, "kind": "text", "anchor": {"kind": "block", "id": 4}}
    out = cp.adapt_for_target(
        rbd, cp.Payload([("annotation", a)], "", "t", "p"), same_model=False)
    assert "anchor" not in out.buffer[0][1]
    json.dumps(out.buffer[0][1])

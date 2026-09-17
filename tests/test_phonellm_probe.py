"""Offline safeguards for the optional hosting probe; no Modal/network imports."""

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("probe", ROOT / "scripts/probe_phonellm_sandbox.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
NOW = 1789680000.0  # September 2026; freeze time so the expiry test stays meaningful.


@pytest.fixture
def receipt(tmp_path):
    db = tmp_path / "spend.sqlite"
    with sqlite3.connect(db) as con:
        con.executescript("""
          CREATE TABLE meta(id INTEGER,cap INTEGER); INSERT INTO meta VALUES(1,200000000000);
          CREATE TABLE opening(id INTEGER,amount INTEGER);
          INSERT INTO opening VALUES(1,20000000000);
          CREATE TABLE requests(id TEXT,reserved INTEGER,actual INTEGER,status TEXT);
          INSERT INTO requests VALUES('probe',5000000000,NULL,'reserved');
        """)
    evidence = tmp_path / "review.txt"
    evidence.write_text("offline fixture; never valid launch evidence")
    return {
        "plan_sha256": probe.sha(probe.plan()),
        "reserved_usd": 5,
        "ledger_path": str(db),
        "reservation_id": "probe",
        "workspace": "offline-fixture",
        "existing_image_id": probe.EXISTING_IMAGE_ID,
        "resource_envelope_verified": True,
        "verified_at_unix": NOW,
        "price_evidence_path": str(evidence),
        "price_evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
    }


def test_envelope_and_fail_closed(receipt):
    planned = probe.plan()
    assert planned["base_compute_envelope_usd"] == 4.177008
    assert probe.check_receipt(receipt, planned, now=NOW) == 5
    for change in (
        {"reserved_usd": 4},
        {"plan_sha256": "stale"},
        {"existing_image_id": "im-another"},
        {"verified_at_unix": NOW - 901},
        {"resource_envelope_verified": False},
    ):
        with pytest.raises(ValueError):
            probe.check_receipt(receipt | change, planned, now=NOW)
    with pytest.raises(ValueError, match="expired"):
        probe.check_receipt(receipt, planned, now=1790812800)
    with sqlite3.connect(receipt["ledger_path"]) as con:
        con.execute("UPDATE requests SET status='settled',actual=0")
    with pytest.raises(ValueError, match="reservation"):
        probe.check_receipt(receipt, planned, now=NOW)


def test_single_dispatch_cleanup_and_copied_receipt_rejected(receipt, tmp_path):
    calls = []
    sandbox = SimpleNamespace(
        object_id="sb-offline",
        stdout=[],
        returncode=0,
        wait=lambda: None,
        terminate=lambda **kw: calls.append(("terminate", kw)),
    )
    fake = SimpleNamespace(
        __version__="offline",
        Workspace=SimpleNamespace(
            from_context=lambda: SimpleNamespace(name="offline-fixture", hydrate=lambda: None)
        ),
        App=SimpleNamespace(lookup=lambda *a, **kw: "app"),
        Image=SimpleNamespace(from_id=lambda image_id: image_id),
        Sandbox=SimpleNamespace(create=lambda *a, **kw: calls.append(("create", kw)) or sandbox),
    )
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    with (
        patch.dict(sys.modules, {"modal": fake}),
        patch.object(probe.time, "time", return_value=NOW),
    ):
        probe.launch(probe.plan(), path, tmp_path / "result")
        copied = tmp_path / "copied.json"
        copied.write_text(path.read_text())
        with pytest.raises(FileExistsError):
            probe.launch(probe.plan(), copied, tmp_path / "result2")
    assert [c[0] for c in calls] == ["create", "terminate"]
    cfg = calls[0][1]
    assert cfg["timeout"] == 1800 and cfg["cpu"] == (4, 4)
    assert cfg["memory"] == (65536, 65536) and cfg["volumes"] == {}
    assert "region" not in cfg and cfg["secrets"] == []
    result = json.loads((tmp_path / "result/result.json").read_text())
    assert not result["rank_eligible"] and result["termination_confirmed"]


def test_worker_compiles_and_shared_schema():
    compile(probe.WORKER, "worker", "exec")
    for payload in probe.plan()["requests"]:
        assert "provider" not in payload and payload["max_tokens"] == 16384
        schema = payload["response_format"]["json_schema"]["schema"]
        assert "wait(1)" in schema["properties"]["action"]["enum"]
        assert "chop(tomato)" in schema["properties"]["action"]["enum"]
        canonical = json.loads(payload["messages"][1]["content"])
        kitchen = canonical["state"]["kitchen"]
        assert set(kitchen["recipe_cards"]) == {o["dish"] for o in kitchen["orders"]}

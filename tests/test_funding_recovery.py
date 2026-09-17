"""Funding recovery tests use copied evidence and temporary ledgers; never APIs."""

import copy
import importlib.util
import io
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_release_v2 as audit  # noqa: E402
import funding_recovery_v2 as funding  # noqa: E402


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def proof(root, minimum=6.24576):
    value = dict(
        checked_at_unix=time.time(),
        read_only=True,
        provider="OpenRouter",
        scope="active_runner_credential",
        endpoints=["https://openrouter.ai/api/v1/credits", "https://openrouter.ai/api/v1/key"],
        diagnosis_sha256=funding.DIAGNOSIS_SHA,
        required_usd=minimum,
        credits_sufficient=True,
        key_limit_sufficient=True,
        jobs=list(funding.ORIGINAL_HASHES),
    )
    path = root / "runs/v2/release-r2/fixture-funded.json"
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def incident(tmp_path):
    # Deliberately preserve exact real incident bytes and frozen source archives.
    paths = list((ROOT / "src").rglob("*.py"))
    paths += list((ROOT / "data/v2/release-r2").glob("*.zip"))
    paths += list((ROOT / "data/v2/release-r2").glob("*-gate.json"))
    paths += [
        ROOT / p
        for p in (
            "data/v2/release-r2/campaign.json",
            "data/v2/release-r2/peer-pause-amendment.json",
            "data/v2/release-r2/roster_canary-manifest.json",
            "data/v2/release-r2-roster.json",
            "data/v2/roster.json",
            "scripts/release_v2.py",
            "scripts/audit_release_v2.py",
            "scripts/audit_roster_v2.py",
            "scripts/funding_recovery_v2.py",
            funding.DIAGNOSIS,
            funding.LIABILITY,
        )
    ]
    paths += [ROOT / f"runs/v2/release-r2/roster_canary/{j}.json" for j in funding.ORIGINAL_HASHES]
    for source in paths:
        dest = tmp_path / source.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    # Historical ledgers only; live campaign ledger is never opened or mutated.
    for name in ("runs/v2/spend.sqlite", "runs/v2/release/spend.sqlite"):
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect((ROOT / name).as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(dest) as target:
                source.backup(target)
    manifest = funding.read(tmp_path / "data/v2/release-r2/roster_canary-manifest.json")
    db = tmp_path / "runs/v2/release-r2/spend.sqlite"
    with sqlite3.connect(db) as con:
        con.executescript("""
          CREATE TABLE meta(id INTEGER PRIMARY KEY,cap INTEGER);
          CREATE TABLE opening(id INTEGER PRIMARY KEY,amount INTEGER);
          CREATE TABLE requests(id TEXT PRIMARY KEY,episode TEXT,stage TEXT,
                                reserved INTEGER,actual INTEGER,status TEXT);
        """)
        con.execute("INSERT INTO meta VALUES(1,200000000000)")
        con.execute("INSERT INTO opening VALUES(1,?)", (audit.money(manifest["opening_balance"]),))
        for job in funding.ORIGINAL_HASHES:
            ep = funding.read(tmp_path / f"runs/v2/release-r2/roster_canary/{job}.json")
            for request in ep["requests"]:
                has_response = "response" in request
                con.execute(
                    "INSERT INTO requests VALUES(?,?,?,?,?,?)",
                    (
                        request["id"],
                        job,
                        "roster_canary",
                        audit.money(request["reserved_usd"]),
                        audit.money(request["cost_usd"]) if has_response else None,
                        "settled" if has_response else "retained_failure",
                    ),
                )
    runner = module("funding_runner_fixture", tmp_path / "scripts/release_v2.py")
    runner.ROOT = tmp_path
    runner.DATA = tmp_path / "data/v2/release-r2"
    runner.RUNS = tmp_path / "runs/v2/release-r2"
    return tmp_path, manifest, runner, proof(tmp_path)


def activate(incident):
    root, manifest, runner, evidence = incident
    runner.authorize_funding_amendment("roster_canary", "Offline fixture only", evidence)
    return funding.verify_amendment(root, manifest, current=True)


def test_exact_402_eligibility_and_unchanged_transport_regex(incident):
    root, manifest, _, _ = incident
    job = next(j for j in manifest["jobs"] if j["id"] == funding.FUNDING_JOB)
    ep = funding.read(root / f"runs/v2/release-r2/roster_canary/{job['id']}.json")
    funding.eligible_funding_failure(ep, job)
    with pytest.raises(ValueError):
        audit.eligible_transport_failure(ep)
    for code in (401, 403, 429, 500):
        altered = copy.deepcopy(ep)
        altered["error"] = f"ProviderError: HTTP {code}; no automatic retry"
        with pytest.raises(ValueError):
            funding.eligible_funding_failure(altered, job)
    for field in ("response", "selection", "cost_usd", "output_tokens"):
        altered = copy.deepcopy(ep)
        altered["requests"][-1][field] = None
        with pytest.raises(ValueError):
            funding.eligible_funding_failure(altered, job)
    with pytest.raises(ValueError):
        funding.eligible_funding_failure(ep, dict(job, id="other"))


def test_chain_preserves_old_evidence_and_rejects_tampering(incident):
    root, manifest, runner, evidence = incident
    old = {p: p.read_bytes() for p in (root / "data/v2/release-r2").iterdir()}
    activate(incident)
    assert all(p.read_bytes() == value for p, value in old.items())
    assert funding.gate_path(root, "roster_canary").name == "roster_canary-funding-gate.json"
    assert funding.gate_path(root, "revised_canary").name == "revised_canary-gate.json"
    with pytest.raises(ValueError, match="already exists"):
        runner.authorize_funding_amendment("roster_canary", "duplicate", evidence)
    targets = (
        "data/v2/release-r2/peer-pause-amendment.json",
        "data/v2/release-r2/roster_canary-manifest.json",
        "data/v2/release-r2-roster.json",
        "src/kitchenrush/v2/environment.py",
        "scripts/funding_recovery_v2.py",
        f"runs/v2/release-r2/roster_canary/{funding.FUNDING_JOB}.json",
    )
    for name in targets:
        path = root / name
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with pytest.raises(ValueError):
            funding.verify_amendment(root, manifest, current=True)
        path.write_bytes(original)
    amendment = funding.read(root / funding.AMENDMENT)
    archive = root / amendment["amended_orchestration"]["archive"]
    archive.write_bytes(archive.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="archive hash"):
        funding.verify_amendment(root, manifest, current=True)


def test_one_recovery_prefix_costs_peer_link_and_live_dispatch_gate(incident):
    root, manifest, runner, evidence = incident
    activate(incident)
    runner.authorize_recovery(
        "roster_canary", funding.FUNDING_JOB, "fixture", funded_evidence=evidence
    )
    with pytest.raises(ValueError, match="already authorized"):
        runner.authorize_recovery(
            "roster_canary", funding.FUNDING_JOB, "fixture", funded_evidence=evidence
        )
    for jid in set(funding.ORIGINAL_HASHES) - {funding.FUNDING_JOB}:
        with pytest.raises(ValueError, match="linkage"):
            runner.authorize_recovery(
                "roster_canary", jid, "fixture", peer_job="wrong", funded_evidence=evidence
            )
        runner.authorize_recovery(
            "roster_canary", jid, "fixture", peer_job=funding.FUNDING_JOB, funded_evidence=evidence
        )
    ledger, _ = audit.ledger_snapshot(
        root / "runs/v2/release-r2/spend.sqlite", manifest["opening_balance"]
    )
    assert ledger[funding.FUNDING_JOB + ":5"][2:] == (6245760000, None, "retained_failure")
    assert sum(row[3] or 0 for row in ledger.values()) == audit.money(0.06318 + 0.089232 + 0.159879)
    jobs = [j for j in manifest["jobs"] if j["id"] in funding.ORIGINAL_HASHES]
    with patch.object(funding, "collect_funded", side_effect=ValueError("not funded")):
        with pytest.raises(ValueError, match="not funded"):
            funding.prepare_dispatch(root, manifest, jobs)
    assert not any(funding.dispatch_path(root, j).exists() for j in jobs)
    db = root / "runs/v2/release-r2/spend.sqlite"
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE requests SET actual=0,status='settled' WHERE id=?",
            (funding.FUNDING_JOB + ":5",),
        )
    with patch.object(funding, "collect_funded", side_effect=AssertionError("must not fetch")):
        with pytest.raises(ValueError, match="liability not fully retained"):
            funding.prepare_dispatch(root, manifest, jobs)
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE requests SET actual=NULL,status='retained_failure' WHERE id=?",
            (funding.FUNDING_JOB + ":5",),
        )
    larger = root / "runs/v2/release-r2/dispatch-proof.json"
    proof_value = funding.read(evidence)
    proof_value["required_usd"] = sum(funding.initial_reserve(manifest, j) for j in jobs)
    larger.write_text(json.dumps(proof_value))
    with patch.object(funding, "collect_funded", return_value=funding.ref(root, larger)):
        funding.prepare_dispatch(root, manifest, jobs)
        with pytest.raises(ValueError, match="already claimed"):
            funding.prepare_dispatch(root, manifest, jobs)
    for job in jobs:
        record = audit.recovery_record(root, manifest, job)
        funding.verify_dispatch(root, manifest, job, record)
        assert record["replacement_id"] == job["id"] + "--attempt2"
        assert not (root / record["replacement_path"]).exists()  # No inference.
    evidence.write_text(json.dumps(proof_value))
    # Authorization evidence is immutable: changing it must invalidate the chain.
    with pytest.raises(ValueError, match="hash differs"):
        funding.prepare_dispatch(root, manifest, jobs)


def test_peer_recovery_rejects_real_stage_exhaustion(incident):
    root, manifest, runner, evidence = incident
    activate(incident)
    runner.authorize_recovery(
        "roster_canary", funding.FUNDING_JOB, "fixture", funded_evidence=evidence
    )
    jid = "roster_canary-claude-sonnet-4-6-60100-3-0-d0.4-tNone"
    # Existing prefix liability fits $7, but another full Sonnet reserve cannot fit.
    with patch.object(audit, "STAGES", dict(audit.STAGES, roster_canary=7)):
        with pytest.raises(ValueError, match="next paused request could not fit"):
            runner.authorize_recovery(
                "roster_canary",
                jid,
                "fixture",
                peer_job=funding.FUNDING_JOB,
                funded_evidence=evidence,
            )


def test_funding_collector_is_get_only_and_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-secret-not-real")
    requests = []

    def response(request, **kwargs):
        requests.append(request)
        data = (
            dict(total_credits=100, total_usage=20)
            if request.full_url.endswith("credits")
            else dict(limit=10, limit_remaining=9)
        )
        return io.BytesIO(json.dumps(dict(data=data)).encode())

    with patch.object(funding.urllib.request, "urlopen", side_effect=response):
        evidence = funding.collect_funded(tmp_path, 6.24576)
    assert [r.get_method() for r in requests] == ["GET", "GET"]
    value = funding.verify_funded(tmp_path, evidence, 6.24576, fresh=True)
    assert value["credits_sufficient"] and value["key_limit_sufficient"]
    text = (tmp_path / evidence["path"]).read_text()
    assert (
        "fixture-secret" not in text and "total_credits" not in text and "total_usage" not in text
    )
    with pytest.raises(ValueError, match="expired"):
        funding.verify_funded(tmp_path, evidence, 6.24576, fresh=True, now=time.time() + 301)
    with pytest.raises(ValueError):
        funding.verify_funded(tmp_path, evidence, 10)


@pytest.mark.parametrize("key_response", [{}, {"limit": True}, {"limit": 1}, {"limit": -1}])
def test_missing_or_invalid_key_metadata_rejected(tmp_path, monkeypatch, key_response):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-not-real")

    def response(request, **kwargs):
        data = (
            dict(total_credits=100, total_usage=0)
            if request.full_url.endswith("credits")
            else key_response
        )
        return io.BytesIO(json.dumps(dict(data=data)).encode())

    with patch.object(funding.urllib.request, "urlopen", side_effect=response):
        with pytest.raises((ValueError, KeyError)):
            funding.collect_funded(tmp_path, 6.24576)

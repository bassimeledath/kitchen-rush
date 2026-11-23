"""Narrow append-only recovery of the recorded roster-canary funding interruption.

No generic retry rule is relaxed. This sidecar cannot dispatch inference or edit
the ledger; the normal runner owns the existing one-replacement lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.request
import uuid
import zipfile

POLICY = "manual-funding-402-replacement-once-1"
PEER_POLICY = "manual-funding-peer-pause-replacement-once-1"
BATCH = "roster_canary"
FUNDING_JOB = "roster_canary-claude-sonnet-4-6-think-60100-3-0-d0.4-tNone"
ORIGINAL_HASHES = {
    FUNDING_JOB: "271572e4ca992094a0c342bbcdfdc0ef1281c2b101f4be52a567b06e0c544ab7",
    "roster_canary-claude-sonnet-4-6-60100-3-0-d0.4-tNone": (
        "60dbc99e133fe215217c5bc4d359a4b64ea13267405d23b32903958297c0bb11"
    ),
    "roster_canary-claude-haiku-4-5-60100-3-0-d0.4-tNone": (
        "e7002d505d39a2167e2d47607e7b2006323c7cb7aa29e0083ce6727d85e5383f"
    ),
}
AMENDMENT = "data/v2/release-r2/funding-pause-amendment.json"
DIAGNOSIS = "runs/v2/release-r2/funding-check-20260917.json"
DIAGNOSIS_SHA = "87165b3aa239ca993daf3ffe2faf2e67dd5e4cbc6befc3d2771e44e2f5bc44da"
LIABILITY = "runs/v2/release-r2/funding-liability-disposition.json"
SIDECAR = "scripts/funding_recovery_v2.py"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(root, path):
    return dict(path=path.relative_to(root).as_posix(), sha256=sha(path))


def checked_ref(root, value):
    path = root / value["path"]
    require(path.resolve().is_relative_to(root.resolve()), "evidence path outside campaign")
    require(sha(path) == value["sha256"], "funding evidence hash differs")
    return path


def gate_path(root, batch):
    suffix = "funding-gate" if batch == BATCH and (root / AMENDMENT).exists() else "gate"
    return root / f"data/v2/release-r2/{batch}-{suffix}.json"


def eligible_funding_failure(ep, job):
    require(
        job["id"] == FUNDING_JOB
        and ep.get("id") == FUNDING_JOB
        and ep.get("status") == "quarantined"
        and ep.get("reason") == "provider_or_accounting_failure"
        and ep.get("error") == "ProviderError: HTTP 402; no automatic retry"
        and len(ep.get("requests", [])) == 6,
        "only the recorded funding 402 may be replaced",
    )
    last = ep["requests"][-1]
    require(
        last["id"] == FUNDING_JOB + ":5"
        and last.get("admitted") is False
        and not any(
            k in last
            for k in (
                "response",
                "selection",
                "cost_usd",
                "input_tokens",
                "output_tokens",
                "post_quality",
                "post_state_hash",
            )
        ),
        "funding failure must have no interpreted response",
    )


def verify_scope(root, manifest):
    require(manifest["batch"] == BATCH, "funding recovery is limited to roster_canary")
    jobs = {j["id"]: j for j in manifest["jobs"]}
    require(set(ORIGINAL_HASHES) <= jobs.keys(), "funding incident jobs missing")
    for job, expected in ORIGINAL_HASHES.items():
        path = root / f"runs/v2/release-r2/{BATCH}/{job}.json"
        require(sha(path) == expected, "funding incident original artifact changed")
    ep = read(root / f"runs/v2/release-r2/{BATCH}/{FUNDING_JOB}.json")
    eligible_funding_failure(ep, jobs[FUNDING_JOB])
    liability = read(root / LIABILITY)
    require(
        liability["request_id"] == FUNDING_JOB + ":5"
        and liability["status"] == "retained_failure"
        and liability["actual_cost_usd"] is None
        and liability["retained_liability_usd"] == 6.24576
        and liability["funding_evidence_sha256"] == DIAGNOSIS_SHA,
        "funding liability disposition differs",
    )


def verify_funded(root, evidence, minimum=0, *, fresh=False, now=None):
    proof = read(checked_ref(root, evidence))
    require(
        proof.get("read_only") is True
        and proof.get("provider") == "OpenRouter"
        and proof.get("scope") == "active_runner_credential"
        and proof.get("endpoints")
        == ["https://openrouter.ai/api/v1/credits", "https://openrouter.ai/api/v1/key"]
        and proof.get("diagnosis_sha256") == DIAGNOSIS_SHA
        and proof.get("credits_sufficient") is True
        and proof.get("key_limit_sufficient") is True
        and type(proof.get("required_usd")) in (int, float)
        and math.isfinite(proof["required_usd"])
        and proof["required_usd"] >= max(minimum, 0.000000001),
        "fresh funded-account proof required",
    )
    checked = proof.get("checked_at_unix")
    require(type(checked) in (int, float) and math.isfinite(checked), "invalid funding timestamp")
    if fresh:
        age = (time.time() if now is None else now) - checked
        require(0 <= age <= 300, "funding proof expired; fetch again before dispatch")
    return proof


def collect_funded(root, minimum, *, jobs=()):
    """GET-only preflight; persist sufficient-funds attestations, never balances/keys."""
    require(math.isfinite(minimum) and minimum > 0, "invalid next-wave reserve")
    key = os.environ.get("OPENROUTER_API_KEY")
    require(bool(key), "missing configured OpenRouter credential")
    results = []
    endpoints = ["https://openrouter.ai/api/v1/credits", "https://openrouter.ai/api/v1/key"]
    for endpoint in endpoints:
        request = urllib.request.Request(endpoint, headers={"Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                results.append(json.load(response)["data"])
        except Exception as exc:
            raise ValueError("read-only funding preflight failed: " + type(exc).__name__) from None
    credits, account_key = results

    def number(value):
        require(type(value) in (int, float) and math.isfinite(value), "invalid funding metadata")
        return value

    remaining = number(credits["total_credits"]) - number(credits["total_usage"])
    require("limit" in account_key, "missing key limit metadata")
    limited = account_key["limit"] is not None
    if limited:
        require(number(account_key["limit"]) >= 0, "invalid key limit metadata")
    key_room = number(account_key["limit_remaining"]) if limited else math.inf
    proof = dict(
        checked_at_unix=time.time(),
        read_only=True,
        provider="OpenRouter",
        scope="active_runner_credential",
        endpoints=endpoints,
        diagnosis_sha256=DIAGNOSIS_SHA,
        required_usd=minimum,
        credits_sufficient=remaining >= minimum,
        key_limit_sufficient=key_room >= minimum,
        key_has_limit=limited,
        jobs=list(jobs),
        balances_omitted=True,
        evidence_limitation="Collector assertion; public data cannot recompute private balances.",
    )
    folder = root / "runs/v2/release-r2/funding-preflights"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex + ".json")
    with path.open("x") as file:
        json.dump(proof, file, indent=2, sort_keys=True)
        file.write("\n")
    evidence = ref(root, path)
    verify_funded(root, evidence, minimum, fresh=True)
    return evidence


def initial_reserve(manifest, job):
    from kitchenrush.v2.providers import Client, Model

    model = Model(**manifest["models"][job["model"]])
    maximum = min(model.max_output, job["token_cap"]) if job["token_cap"] else model.max_output
    return Client(model).bound({"max_tokens": maximum})[1]


def dispatch_path(root, job):
    return root / f"runs/v2/release-r2/funding-dispatch/dispatch-{job['id']}--attempt2.json"


def verify_dispatch(root, manifest, job, disposition):
    record = read(dispatch_path(root, job))
    require(
        record["job"] == job["id"]
        and record["replacement_id"] == job["id"] + "--attempt2"
        and record["amendment_sha256"] == disposition["amendment_sha256"]
        and record["manifest_sha256"]
        == sha(root / f"data/v2/release-r2/{manifest['batch']}-manifest.json"),
        "funding dispatch identity differs",
    )
    proof = verify_funded(root, record["funded_evidence"], initial_reserve(manifest, job))
    require(job["id"] in proof["jobs"], "funding dispatch proof omits job")
    require(
        0 <= record["dispatch_checked_at_unix"] - proof["checked_at_unix"] <= 300,
        "funding proof was stale at dispatch",
    )
    return record


def prepare_dispatch(root, manifest, jobs):
    """Only affected replacements trigger this read-only funding API preflight."""
    from audit_release_v2 import recovery_record

    funding = []
    for job in jobs:
        record = recovery_record(root, manifest, job)
        if record and record["policy"] in (POLICY, PEER_POLICY):
            require(
                not dispatch_path(root, job).exists(),
                "funding dispatch already claimed; audit before retry",
            )
            funding.append((job, record))
    if not funding:
        return
    verify_retained_incident(root, manifest)
    minimum = sum(
        initial_reserve(manifest, job)
        for job in jobs
        if manifest["models"][job["model"]]["provider"] != "TypeSafe"
    )
    evidence = collect_funded(root, minimum, jobs=[j["id"] for j in jobs])
    verify_funded(root, evidence, minimum, fresh=True)
    verify_retained_incident(root, manifest)
    for job, disposition in funding:
        path = dispatch_path(root, job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x") as file:
            json.dump(
                dict(
                    job=job["id"],
                    replacement_id=job["id"] + "--attempt2",
                    amendment_sha256=disposition["amendment_sha256"],
                    manifest_sha256=sha(
                        root / f"data/v2/release-r2/{manifest['batch']}-manifest.json"
                    ),
                    funded_evidence=evidence,
                    dispatch_checked_at_unix=time.time(),
                ),
                file,
                indent=2,
                sort_keys=True,
            )
            file.write("\n")


def verify_retained_incident(root, manifest):
    """Recheck real held liability and all original prefixes immediately before dispatch."""
    from audit_release_v2 import ledger_snapshot, verify_episode

    verify_scope(root, manifest)
    ledger, _ = ledger_snapshot(
        root / "runs/v2/release-r2/spend.sqlite", manifest["opening_balance"]
    )
    for job in manifest["jobs"]:
        if job["id"] in ORIGINAL_HASHES:
            ep = read(root / f"runs/v2/release-r2/{BATCH}/{job['id']}.json")
            verify_episode(
                ep,
                job,
                manifest,
                ledger,
                set(),
                funding=job["id"] == FUNDING_JOB,
                paused=job["id"] != FUNDING_JOB,
            )


def verify_amendment(root, manifest, *, current=False):
    from audit_release_v2 import verify_amendment as verify_peer
    from audit_release_v2 import verify_archive, verify_campaign

    from kitchenrush.v2.environment import digest

    verify_campaign(root, manifest)
    verify_archive(root, manifest)
    parent = verify_peer(root, manifest)
    path = root / AMENDMENT
    amendment = read(path)
    campaign_path = root / "data/v2/release-r2/campaign.json"
    peer = read(root / parent["path"])
    require(
        amendment["policy"] == POLICY
        and amendment["baseline_campaign_sha256"] == sha(campaign_path)
        and amendment["parent_amendment"] == parent
        and amendment["baseline_orchestration"] == manifest["orchestration"]
        and amendment["parent_orchestration"] == peer["amended_orchestration"]
        and amendment["protocol_hash"] == manifest["protocol_hash"]
        and amendment["scope"]
        == dict(batch=BATCH, funding_job=FUNDING_JOB, original_artifact_sha256=ORIGINAL_HASHES)
        and bool(amendment["review_note"].strip()),
        "funding amendment chain/scope differs",
    )
    incident_manifest = read(root / f"data/v2/release-r2/{BATCH}-manifest.json")
    require(
        sha(root / f"data/v2/release-r2/{BATCH}-manifest.json")
        == amendment["incident_manifest_sha256"],
        "funding incident manifest changed",
    )
    verify_archive(root, incident_manifest)
    verify_scope(root, incident_manifest)
    require(
        sha(root / LIABILITY) == amendment["liability_disposition"]["sha256"],
        "retained funding liability evidence changed",
    )
    require(amendment["liability_disposition"]["path"] == LIABILITY, "wrong liability evidence")
    diagnosis = read(checked_ref(root, amendment["diagnosis"]))
    require(
        diagnosis
        == dict(
            source_sha256=DIAGNOSIS_SHA,
            credits_exhausted=True,
            read_only=True,
            provider="OpenRouter",
        ),
        "funding exhaustion attestation changed",
    )
    verify_funded(root, amendment["funded_evidence"], 6.24576)
    required_preserved = {
        manifest["orchestration"]["archive"],
        parent["source_archive"],
        "data/v2/release-r2/offline-gate.json",
        "data/v2/release-r2/revised_canary-gate.json",
    }
    preserved_paths = [evidence["path"] for evidence in amendment["preserved_evidence"]]
    require(
        len(preserved_paths) == len(set(preserved_paths))
        and required_preserved <= set(preserved_paths),
        "funding amendment omits preserved checkpoints",
    )
    for evidence in amendment["preserved_evidence"]:
        checked_ref(root, evidence)
    descriptor = amendment["amended_orchestration"]
    scripts = set(manifest["orchestration"]["scripts"]) | {SIDECAR}
    require(
        descriptor["version"] == 1 and set(descriptor["scripts"]) == scripts,
        "funding amendment script allowlist differs",
    )
    # The roster checker is unaffected by an operational funding pause.
    roster_checker = "scripts/audit_roster_v2.py"
    require(
        descriptor["scripts"][roster_checker]
        == peer["amended_orchestration"]["scripts"][roster_checker],
        "funding amendment changed roster checker",
    )
    identity = digest(dict(protocol_hash=manifest["protocol_hash"], scripts=descriptor["scripts"]))
    require(
        descriptor["archive"] == f"data/v2/release-r2/source-{identity}.zip",
        "funding archive path differs",
    )
    archive = root / descriptor["archive"]
    require(sha(archive) == amendment["archive_sha256"], "funding source archive hash differs")
    package = {p.relative_to(root).as_posix() for p in (root / "src").rglob("*.py")}
    with zipfile.ZipFile(archive) as bundle:
        require(
            len(bundle.namelist()) == len(set(bundle.namelist()))
            and set(bundle.namelist()) == package | scripts,
            "funding archive membership differs",
        )
        for name in package:
            require(bundle.read(name) == (root / name).read_bytes(), "funding changes frozen core")
        for name in scripts:
            require(
                hashlib.sha256(bundle.read(name)).hexdigest() == descriptor["scripts"][name],
                "funding driver hash differs",
            )
            if current:
                require(
                    bundle.read(name) == (root / name).read_bytes(),
                    "unlogged funding driver change",
                )
    return dict(
        path=AMENDMENT,
        sha256=sha(path),
        source_archive=descriptor["archive"],
        source_archive_sha256=sha(archive),
        evidence_files=[
            amendment[k]["path"] for k in ("diagnosis", "liability_disposition", "funded_evidence")
        ],
    )


def authorize(root, manifest, descriptor, archive_writer, note, funded_evidence):
    """Called explicitly by runner CLI only; preparation never invokes this on real data."""
    from audit_release_v2 import ledger_snapshot, verify_archive, verify_campaign, verify_episode
    from audit_release_v2 import verify_amendment as verify_peer

    require(bool(note.strip()), "funding amendment needs a review note")
    require(not (root / AMENDMENT).exists(), "funding amendment already exists")
    verify_campaign(root, manifest)
    verify_archive(root, manifest)
    parent = verify_peer(root, manifest)
    verify_scope(root, manifest)
    evidence = ref(root, funded_evidence)
    verify_funded(root, evidence, 6.24576, fresh=True)
    require(sha(root / DIAGNOSIS) == DIAGNOSIS_SHA, "original exhaustion proof changed")
    old = read(root / DIAGNOSIS)
    require(
        old["read_only"] and old["credits"]["total_usage"] >= old["credits"]["total_credits"],
        "original funding evidence is not exhausted",
    )
    ledger, _ = ledger_snapshot(
        root / "runs/v2/release-r2/spend.sqlite", manifest["opening_balance"]
    )
    for job in manifest["jobs"]:
        if job["id"] in ORIGINAL_HASHES:
            ep = read(root / f"runs/v2/release-r2/{BATCH}/{job['id']}.json")
            verify_episode(
                ep,
                job,
                manifest,
                ledger,
                set(),
                funding=job["id"] == FUNDING_JOB,
                paused=job["id"] != FUNDING_JOB,
            )
    folder = root / "data/v2/release-r2"
    preserved = [ref(root, p) for p in sorted(folder.glob("*-gate.json"))]
    preserved += [
        ref(root, root / manifest["orchestration"]["archive"]),
        ref(root, root / parent["source_archive"]),
    ]
    archive_writer(dict(orchestration=descriptor))
    public_diagnosis = folder / "funding-exhaustion-attestation.json"
    with public_diagnosis.open("x") as file:
        json.dump(
            dict(
                source_sha256=DIAGNOSIS_SHA,
                credits_exhausted=True,
                read_only=True,
                provider="OpenRouter",
            ),
            file,
            indent=2,
            sort_keys=True,
        )
        file.write("\n")
    record = dict(
        policy=POLICY,
        baseline_campaign_sha256=sha(folder / "campaign.json"),
        incident_manifest_sha256=sha(folder / f"{BATCH}-manifest.json"),
        parent_amendment=parent,
        baseline_orchestration=manifest["orchestration"],
        parent_orchestration=read(root / parent["path"])["amended_orchestration"],
        amended_orchestration=descriptor,
        archive_sha256=sha(root / descriptor["archive"]),
        protocol_hash=manifest["protocol_hash"],
        review_note=note,
        scope=dict(batch=BATCH, funding_job=FUNDING_JOB, original_artifact_sha256=ORIGINAL_HASHES),
        diagnosis=ref(root, public_diagnosis),
        liability_disposition=ref(root, root / LIABILITY),
        funded_evidence=evidence,
        preserved_evidence=preserved,
    )
    with (root / AMENDMENT).open("x") as file:
        json.dump(record, file, indent=2, sort_keys=True)
        file.write("\n")
    return verify_amendment(root, manifest, current=True)

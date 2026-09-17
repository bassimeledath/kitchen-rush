#!/usr/bin/env python3
"""Package audited benchmark traces and accounting for offline independent verification."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from audit_release_v2 import audit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def sha_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def public_json(content):
    value = json.loads(content)

    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in {
                    "workspace",
                    "workspace_id",
                    "workspace_name",
                    "account_id",
                    "account_name",
                    "api_key",
                    "access_token",
                    "secret",
                    "password",
                    "balance",
                    "ledger_path",
                }:
                    raise ValueError("private account field in supplemental evidence")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)

    inspect(value)
    return value


def reject_credentials(payloads, secrets):
    def inspect(content):
        if any(secret in content for secret in secrets):
            raise ValueError("credential scan rejected evidence export")
        if content.startswith(b"PK\x03\x04"):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for name in archive.namelist():
                    inspect(archive.read(name))

    for content in payloads.values():
        inspect(content)


def diagnostic_payloads(root):
    """Snapshot only terminal attempts; check each archived version against its own plan."""
    folder = root / "runs/v2/phonellm-episodes-20260917"
    payloads, excluded_pending = {}, []

    def add(path):
        content = path.read_bytes()
        if path.suffix == ".json":
            public_json(content)
        payloads[path.relative_to(root).as_posix()] = content
        return content

    for attempt, suffix in (("execution", "1"), ("execution-attempt2", "2")):
        out = folder / attempt
        result_path = out / "result.json"
        if not result_path.exists():
            continue
        raw_result = result_path.read_bytes()
        result = public_json(raw_result)
        terminal = result.get("local_after_terminate_unix") is not None
        if result.get("sandbox_id"):
            terminal = terminal and result.get("termination_confirmed") is True
        elif terminal:
            diagnosis_path = folder / "failure-diagnosis.json"
            if attempt != "execution" or not diagnosis_path.is_file():
                terminal = False
            else:
                diagnosis = public_json(diagnosis_path.read_bytes())
                terminal = (
                    diagnosis.get("validation_before_sandbox_creation_rpc") is True
                    and diagnosis["original_artifact_sha256"]["result.json"]
                    == hashlib.sha256(raw_result).hexdigest()
                )
        if not terminal:
            excluded_pending.append(out.relative_to(root).as_posix())
            continue
        plan_raw = (out / "plan.json").read_bytes()
        planned = public_json(plan_raw)
        source = (out / "source.zip").read_bytes()
        if (
            result.get("reservation_id") != "phonellm-full-episodes-20260917-" + suffix
            or result.get("plan_sha256") != sha_json(planned)
            or hashlib.sha256(source).hexdigest() != planned["source_archive_sha256"]
        ):
            raise ValueError("diagnostic source or plan binding mismatch")
        if not result.get("sandbox_id"):
            for filename, content in (
                ("result.json", raw_result),
                ("plan.json", plan_raw),
                ("source.zip", source),
            ):
                if (
                    diagnosis["original_artifact_sha256"].get(filename)
                    != hashlib.sha256(content).hexdigest()
                ):
                    raise ValueError("local rejection diagnosis artifact mismatch")
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(
                Path(n).is_absolute()
                or ".." in Path(n).parts
                or not (
                    n.startswith("src/")
                    and n.endswith(".py")
                    or n
                    in (
                        "scripts/phonellm_episode_diagnostic.py",
                        "scripts/probe_phonellm_sandbox.py",
                    )
                )
                for n in names
            ):
                raise ValueError("unexpected diagnostic source archive member")
            script = archive.read("scripts/phonellm_episode_diagnostic.py")
            guard = archive.read("scripts/probe_phonellm_sandbox.py")
            tree = ast.parse(script)
            bootstrap = next(
                ast.literal_eval(n.value)
                for n in tree.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "BOOTSTRAP" for t in n.targets)
            )
            core = ["engine.py", "config.py", "procgen.py", "oracle.py", "tools.py"]
            core += sorted(
                n.removeprefix("src/kitchenrush/")
                for n in names
                if re.fullmatch(r"src/kitchenrush/v2/[^/]+\.py", n)
            )
            protocol = sha_json(
                {n: hashlib.sha256(archive.read("src/kitchenrush/" + n)).hexdigest() for n in core}
            )
            if (
                hashlib.sha256(guard).hexdigest() != planned["guard_source_sha256"]
                or hashlib.sha256(bootstrap.encode()).hexdigest() != planned["worker_sha256"]
                or protocol != planned["protocol_hash"]
            ):
                raise ValueError("diagnostic archived implementation mismatch")
        for path, content in (
            (result_path, raw_result),
            (out / "plan.json", plan_raw),
            (out / "source.zip", source),
        ):
            payloads[path.relative_to(root).as_posix()] = content
        expected_ids = {job["id"] for job in planned["jobs"]}
        for ep_path in (out / "episodes").glob("*.json"):
            ep = public_json(ep_path.read_bytes())
            if ep_path.stem not in expected_ids or ep["id"] != ep_path.stem:
                raise ValueError("unexpected diagnostic episode")
            add(ep_path)
        review_name = "preflight-review.json" if suffix == "1" else "retry-preflight-review.json"
        review_path = folder / review_name
        if review_path.is_file():
            review = public_json(review_path.read_bytes())
            if any(
                review.get(k) != expected
                for k, expected in {
                    "plan_sha256": sha_json(planned),
                    "script_sha256": hashlib.sha256(script).hexdigest(),
                    "source_archive_sha256": planned["source_archive_sha256"],
                }.items()
            ):
                raise ValueError("diagnostic preflight binding mismatch")
            add(review_path)
        public_names = (
            ("plan.json", "failure-diagnosis.json", "accounting-disposition.json")
            if suffix == "1"
            else (
                "retry-plan.json",
                "accounting-disposition-attempt2.json",
                "termination-attestation.json",
                "stdout-failure-diagnosis.json",
            )
        )
        for name in public_names:
            path = folder / name
            if path.is_file():
                add(path)
        sidecar = root / (
            "runs/v2/release-r2/external/phonellm-full-episodes-20260917-" + suffix + ".json"
        )
        if sidecar.is_file():
            add(sidecar)
        billing_path = folder / "gross-billing-attempt2.json"
        if suffix == "2" and billing_path.is_file():
            billing = public_json(billing_path.read_bytes())
            if billing.get("finished_at_utc") and billing.get("status") != "fetch_started":
                for helper, field in (
                    ("reconcile_phonellm_episodes.py", "fetch_script_sha256"),
                    ("reconcile_phonellm_probe.py", "shared_helper_sha256"),
                ):
                    helper_path = root / "scripts" / helper
                    if hashlib.sha256(helper_path.read_bytes()).hexdigest() != billing[field]:
                        raise ValueError("diagnostic billing helper binding mismatch")
                    add(helper_path)
                if billing.get("result_sha256") != hashlib.sha256(raw_result).hexdigest():
                    raise ValueError("diagnostic billing result binding mismatch")
                add(billing_path)
    return payloads, excluded_pending


def cpu_transport_payloads(root):
    """Export terminal synthetic transport evidence only, never a capability cohort."""
    folder = root / "runs/v2/phonellm-cpu-transport-20260917"
    out = folder / "execution"
    result_path = out / "result.json"
    if not result_path.is_file():
        return {}, []
    result = public_json(result_path.read_bytes())
    if not (
        result.get("termination_confirmed") is True and result.get("local_after_terminate_unix")
    ):
        return {}, [out.relative_to(root).as_posix()]
    payloads = {}

    def add(path):
        content = path.read_bytes()
        if path.suffix == ".json":
            public_json(content)
        payloads[path.relative_to(root).as_posix()] = content
        return content

    planned = public_json(add(out / "plan.json"))
    add(result_path)
    review = public_json(add(folder / "preflight-review.json"))
    for record in (result, planned, review):
        if not (
            record.get("inference_executed") is False
            and record.get("synthetic_responses") is True
            and record.get("rank_eligible") is False
            and record.get("b80_eligible") is False
        ):
            raise ValueError("CPU transport evidence must be explicitly synthetic and unranked")
    if not (
        result.get("kind") == planned.get("kind") == "transport_selftest"
        and result.get("reservation_id") == "phonellm-cpu-transport-20260917-1"
        and result.get("plan_sha256") == review.get("plan_sha256") == sha_json(planned)
        and review.get("source_archive_sha256") == planned["source_archive_sha256"]
    ):
        raise ValueError("CPU transport plan binding mismatch")
    source = add(out / "source.zip")
    if hashlib.sha256(source).hexdigest() != planned["source_archive_sha256"]:
        raise ValueError("CPU transport source archive mismatch")
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        names = archive.namelist()
        allowed_scripts = {
            "scripts/phonellm_cpu_transport_check.py",
            "scripts/phonellm_durable_recovery.py",
            "scripts/phonellm_episode_diagnostic.py",
            "scripts/probe_phonellm_sandbox.py",
        }
        if (
            len(names) != len(set(names))
            or not allowed_scripts <= set(names)
            or any(
                Path(n).is_absolute()
                or ".." in Path(n).parts
                or not (n in allowed_scripts or n.startswith("src/") and n.endswith(".py"))
                for n in names
            )
        ):
            raise ValueError("unexpected CPU transport source member")
        if set(review["source_file_sha256"]) != allowed_scripts or any(
            hashlib.sha256(archive.read(name)).hexdigest() != digest
            for name, digest in review["source_file_sha256"].items()
        ):
            raise ValueError("CPU transport archived implementation mismatch")
    ready = public_json(add(out / "READY.json"))
    artifacts = add(out / "artifacts.zip")
    if not (
        ready["plan_sha256"] == sha_json(planned)
        and ready["archive_sha256"] == hashlib.sha256(artifacts).hexdigest()
        and ready["archive_bytes"] == len(artifacts)
        and ready["worker_error"] is None
    ):
        raise ValueError("CPU transport artifact transfer mismatch")
    with zipfile.ZipFile(io.BytesIO(artifacts)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(
            Path(n).is_absolute() or ".." in Path(n).parts for n in names
        ):
            raise ValueError("unsafe CPU transport artifact archive")
        manifest = public_json(archive.read("MANIFEST.json"))
        if set(names) != set(manifest) | {"MANIFEST.json"} or any(
            hashlib.sha256(archive.read(name)).hexdigest() != digest
            for name, digest in manifest.items()
        ):
            raise ValueError("CPU transport artifact member mismatch")
        for name in names:
            if name.endswith(".json"):
                public_json(archive.read(name))
        if public_json(archive.read("plan.json")) != planned or public_json(
            archive.read("runtime.json")
        ) != dict(device="CPU", inference_executed=False, responses="scripted wait actions"):
            raise ValueError("CPU transport runtime mismatch")
    # The receipt contains local account/workspace values and is deliberately excluded.
    for path in (
        folder / "accounting-disposition.json",
        root / "runs/v2/release-r2/external/phonellm-cpu-transport-20260917-1.json",
    ):
        if path.is_file():
            add(path)
    return payloads, []


def durable_recovery_payloads(root):
    """Snapshot terminal, unranked recovery evidence and every hash-bound prior prefix."""
    folder = root / "runs/v2/phonellm-durable-recovery-20260917"
    out = folder / "execution"
    if not (out / "result.json").is_file():
        return {}, []
    result = public_json((out / "result.json").read_bytes())
    if not (
        result.get("termination_confirmed") is True and result.get("local_after_terminate_unix")
    ):
        return {}, [out.relative_to(root).as_posix()]
    payloads = {}

    def add(path):
        content = path.read_bytes()
        if path.suffix == ".json":
            public_json(content)
        payloads[path.relative_to(root).as_posix()] = content
        return content

    add(out / "result.json")
    planned = public_json(add(out / "plan.json"))
    review = public_json(add(folder / "preflight-review.json"))
    if not (
        result.get("kind") == planned.get("kind") == "unranked_phonellm_durable_recovery_v1"
        and all(
            r.get("rank_eligible") is False and r.get("b80_eligible") is False
            for r in (result, planned)
        )
        and result["plan_sha256"] == review["plan_sha256"] == sha_json(planned)
        and planned["scheduled_kitchens"] == 3
        and planned["recovery_jobs"] == 2
        and planned["replacement_affected"] is True
    ):
        raise ValueError("durable recovery plan or eligibility mismatch")
    source = add(out / "source.zip")
    if (
        hashlib.sha256(source).hexdigest() != planned["source_archive_sha256"]
        or review["source_archive_sha256"] != planned["source_archive_sha256"]
    ):
        raise ValueError("durable recovery source mismatch")
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        names = archive.namelist()
        helpers = {
            "scripts/phonellm_durable_recovery.py",
            "scripts/phonellm_episode_diagnostic.py",
            "scripts/probe_phonellm_sandbox.py",
        }
        if (
            len(names) != len(set(names))
            or not helpers <= set(names)
            or any(
                Path(n).is_absolute()
                or ".." in Path(n).parts
                or not (n in helpers or n.startswith("src/") and n.endswith(".py"))
                for n in names
            )
        ):
            raise ValueError("unsafe durable recovery source")
        if set(review["source_file_sha256"]) != helpers or any(
            hashlib.sha256(archive.read(n)).hexdigest() != digest
            for n, digest in review["source_file_sha256"].items()
        ):
            raise ValueError("durable recovery helper mismatch")
        tree = ast.parse(archive.read("scripts/phonellm_durable_recovery.py"))
        bootstrap = next(
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "BOOTSTRAP" for t in n.targets)
        )
        core = ["engine.py", "config.py", "procgen.py", "oracle.py", "tools.py"]
        core += sorted(
            n.removeprefix("src/kitchenrush/")
            for n in names
            if re.fullmatch(r"src/kitchenrush/v2/[^/]+\.py", n)
        )
        if (
            hashlib.sha256(bootstrap.encode()).hexdigest() != planned["worker_sha256"]
            or sha_json(
                {n: hashlib.sha256(archive.read("src/kitchenrush/" + n)).hexdigest() for n in core}
            )
            != planned["protocol_hash"]
        ):
            raise ValueError("durable recovery archived protocol mismatch")
    prior_usage = {}
    for name, digest in planned["prior_artifacts"].items():
        if (
            not name.startswith("runs/v2/phonellm-episodes-20260917/execution-attempt2/")
            or ".." in Path(name).parts
        ):
            raise ValueError("unexpected durable recovery prior path")
        raw = add(root / name)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("durable recovery prior artifact mismatch")
        if "/episodes/" in name:
            episode = public_json(raw)
            requests = episode["requests"]
            usage = [r["response"]["usage"] for r in requests if "response" in r]
            prior_usage[episode["id"]] = dict(
                known_input_tokens=sum(u["prompt_tokens"] for u in usage),
                known_output_tokens=sum(u["completion_tokens"] for u in usage),
                unfinished_request_count=sum("response" not in r for r in requests),
                unknown_unobserved_usage=episode["status"] != "complete",
            )
    if prior_usage != planned["prior_known_usage"]:
        raise ValueError("durable recovery prior usage mismatch")
    if result.get("verified"):
        ready = public_json(add(out / "READY.json"))
        artifacts = add(out / "artifacts.zip")
        if (
            ready["plan_sha256"] != sha_json(planned)
            or ready["archive_sha256"] != hashlib.sha256(artifacts).hexdigest()
            or ready["archive_bytes"] != len(artifacts)
            or ready["worker_error"] is not None
        ):
            raise ValueError("durable recovery transfer mismatch")
        with zipfile.ZipFile(io.BytesIO(artifacts)) as archive:
            names = archive.namelist()
            manifest = public_json(archive.read("MANIFEST.json"))
            if (
                len(names) != len(set(names))
                or set(names) != set(manifest) | {"MANIFEST.json"}
                or any(Path(n).is_absolute() or ".." in Path(n).parts for n in names)
                or any(
                    hashlib.sha256(archive.read(n)).hexdigest() != digest
                    for n, digest in manifest.items()
                )
            ):
                raise ValueError("durable recovery artifact member mismatch")
            for name in names:
                if name.endswith(".json"):
                    public_json(archive.read(name))
            runtime = public_json(archive.read("runtime.json"))
            if (
                public_json(archive.read("plan.json")) != planned
                or runtime["sglang_version"] != "0.5.18"
                or "B200" not in runtime["gpu"]
                or runtime["revision"] != planned["revision"]
            ):
                raise ValueError("durable recovery runtime mismatch")
            episodes = [
                public_json(archive.read("episodes/" + job["id"] + ".json"))
                for job in planned["jobs"]
            ]
            for ep in episodes:
                for field in ("input_tokens", "output_tokens"):
                    if result[field][ep["id"]] != ep[field]:
                        raise ValueError("durable recovery result usage mismatch")
            for eid in set(prior_usage) | {ep["id"] for ep in episodes}:
                prior = prior_usage.get(eid, {})
                expected = dict(
                    input_tokens=prior.get("known_input_tokens", 0)
                    + result["input_tokens"].get(eid, 0),
                    output_tokens=prior.get("known_output_tokens", 0)
                    + result["output_tokens"].get(eid, 0),
                    unknown_unobserved_prior_usage=prior.get("unknown_unobserved_usage", False),
                )
                if result["all_attempt_known_usage"][eid] != expected:
                    raise ValueError("durable recovery all-attempt usage mismatch")
    for path in (
        folder / "accounting-disposition.json",
        root / "runs/v2/release-r2/external" / (result["reservation_id"] + ".json"),
    ):
        if path.is_file():
            add(path)
    return payloads, []


def billing_v2_payloads(root):
    """Finished read-only billing attempts; started records require interruption evidence."""
    payloads = {}
    for folder, filename, result_relative in (
        ("phonellm-probe-20260917", "gross-billing-fetch-v2.json", "execution/result.json"),
        (
            "phonellm-episodes-20260917",
            "gross-billing-attempt2-v2.json",
            "execution-attempt2/result.json",
        ),
    ):
        base = root / "runs/v2" / folder
        path = base / filename
        if not path.is_file():
            continue
        evidence = public_json(path.read_bytes())
        if not evidence.get("finished_at_utc") or evidence.get("status") == "fetch_started":
            continue
        paths = [path]
        expected = {"reconcile_phonellm_probe.py", "reconcile_phonellm_episodes.py"}
        if set(evidence["helper_sha256"]) != expected:
            raise ValueError("versioned billing helper set mismatch")
        for name, digest in dict(
            evidence["helper_sha256"],
            **{"reconcile_phonellm_billing_v2.py": evidence["fetch_script_sha256"]},
        ).items():
            helper = root / "scripts" / name
            if hashlib.sha256(helper.read_bytes()).hexdigest() != digest:
                raise ValueError("versioned billing helper hash mismatch")
            paths.append(helper)
        result_path = base / result_relative
        if hashlib.sha256(result_path.read_bytes()).hexdigest() != evidence["result_sha256"]:
            raise ValueError("versioned billing result hash mismatch")
        result = public_json(result_path.read_bytes())
        if not (
            result.get("termination_confirmed") is True and result.get("local_after_terminate_unix")
        ):
            raise ValueError("versioned billing needs terminal compute evidence")
        if folder.startswith("phonellm-probe"):
            old = base / "gross-billing-fetch.json"
            interrupted = base / "billing-fetch-interruption.json"
            record = public_json(interrupted.read_bytes())
            digest = hashlib.sha256(old.read_bytes()).hexdigest()
            if not (
                record.get("terminal_process_observed") is True
                and record["original_fetch_sha256"]
                == evidence["prior_interrupted_fetch_sha256"]
                == digest
                and hashlib.sha256(interrupted.read_bytes()).hexdigest()
                == evidence["interruption_sha256"]
                and public_json(old.read_bytes())["status"] == "fetch_started"
            ):
                raise ValueError("versioned billing interruption binding mismatch")
            paths.extend([old, interrupted])
        for item in paths:
            content = item.read_bytes()
            if item.suffix == ".json":
                public_json(content)
            payloads[item.relative_to(root).as_posix()] = content
    return payloads


def funding_interruption_payloads(root):
    """Preserve unranked expenses even while the roster checkpoint cannot pass."""
    record_path = root / "data/v2/release-r2/roster_canary-interruption.json"
    if not record_path.is_file():
        return {}
    record = public_json(record_path.read_bytes())
    manifest_path = root / "data/v2/release-r2/roster_canary-manifest.json"
    if (
        record.get("kind") != "funding_interruption"
        or record.get("http_status") != 402
        or record["manifest_sha256"] != hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    ):
        raise ValueError("funding interruption manifest binding mismatch")
    paths = [record_path, manifest_path]
    expected = {
        f"runs/v2/release-r2/roster_canary/roster_canary-{model}-60100-3-0-d0.4-tNone.json"
        for model in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-4-6-think")
    }
    observations = record["observations"]
    if len(observations) != 3 or {o["path"] for o in observations} != expected:
        raise ValueError("unexpected funding interruption scope")
    for observation in observations:
        path = root / observation["path"]
        raw = path.read_bytes()
        episode = public_json(raw)
        if (
            hashlib.sha256(raw).hexdigest() != observation["sha256"]
            or episode["status"] not in ("incomplete", "quarantined")
            or observation.get("rank_eligible") is not False
        ):
            raise ValueError("funding interruption trace binding mismatch")
        paths.append(path)
    disposition = root / "runs/v2/release-r2/funding-liability-disposition.json"
    if disposition.is_file():
        paths.append(disposition)
    payloads = {}
    for path in paths:
        content = path.read_bytes()
        public_json(content)
        payloads[path.relative_to(root).as_posix()] = content
    # The referenced raw account balance check stays local; its hash is provenance only.
    return payloads


def export(batches, output):
    gates = {batch: audit(batch, write_gate=False) for batch in batches}
    paths = {
        ROOT / "data/v2/release-r2-roster.json",
        ROOT / "data/v2/roster.json",
        ROOT / "data/v2/release-r2/campaign.json",
    }
    for batch, gate in gates.items():
        paths.update((ROOT / "runs/v2/release-r2" / batch).rglob("*.json"))
        paths.add(ROOT / f"data/v2/release-r2/{batch}-manifest.json")
        gate_suffix = "funding-gate" if gate.get("funding_operational_amendment") else "gate"
        paths.add(ROOT / f"data/v2/release-r2/{batch}-{gate_suffix}.json")
        paths.add(ROOT / gate["source_archive"])
        if gate.get("operational_amendment"):
            paths.add(ROOT / gate["operational_amendment"]["path"])
            paths.add(ROOT / gate["operational_amendment"]["source_archive"])
        if gate.get("funding_operational_amendment"):
            amendment = gate["funding_operational_amendment"]
            paths.add(ROOT / amendment["path"])
            paths.add(ROOT / amendment["source_archive"])
            for name in amendment.get("evidence_files", []):
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("unsafe funding evidence path")
                public_json((ROOT / name).read_bytes())
                paths.add(ROOT / name)
        paths.update((ROOT / f"data/v2/release-r2/{batch}-recoveries").glob("*.json"))
    # The hosting probe shares the campaign ceiling but is not a scored episode.
    # Export only its public task, reviewed limits and sanitized outcome, never the
    # local receipt (which contains account identity and machine-specific paths).
    probe = ROOT / "runs/v2/phonellm-probe-20260917"
    supplemental = []
    if (probe / "preflight-review.json").is_file():
        script = ROOT / "scripts/probe_phonellm_sandbox.py"
        review = json.loads((probe / "preflight-review.json").read_text())
        if hashlib.sha256(script.read_bytes()).hexdigest() != review["script_sha256"]:
            raise ValueError("hosting probe source changed after preflight")
        for relative in (
            "preflight-plan.json",
            "preflight-review.json",
            "execution/plan.json",
            "execution/result.json",
            "billing-reconciliation.json",
            "liability-disposition.json",
            "resource-meter-observation.json",
        ):
            path = probe / relative
            if path.is_file():
                paths.add(path)
                supplemental.append(path.relative_to(ROOT).as_posix())
        paths.add(script)
        sidecar = ROOT / "runs/v2/release-r2/external/phonellm-sandbox-probe-20260917-1.json"
        if sidecar.is_file():
            paths.add(sidecar)
            supplemental.append(sidecar.relative_to(ROOT).as_posix())
        billing_fetch = probe / "gross-billing-fetch.json"
        if billing_fetch.is_file():
            fetch_script = ROOT / "scripts/reconcile_phonellm_probe.py"
            evidence = public_json(billing_fetch.read_bytes())
            if evidence.get("finished_at_utc") and evidence.get("status") != "fetch_started":
                if (
                    hashlib.sha256(fetch_script.read_bytes()).hexdigest()
                    != evidence["fetch_script_sha256"]
                ):
                    raise ValueError("billing fetch source changed after evidence collection")
                paths.add(fetch_script)
                paths.add(billing_fetch)
                supplemental.append(billing_fetch.relative_to(ROOT).as_posix())
    payloads = {str(p.relative_to(ROOT)): p.read_bytes() for p in sorted(paths)}
    diagnostic, pending_diagnostics = diagnostic_payloads(ROOT)
    payloads.update(diagnostic)
    supplemental.extend(sorted(diagnostic))
    cpu, pending_cpu = cpu_transport_payloads(ROOT)
    payloads.update(cpu)
    supplemental.extend(sorted(cpu))
    pending_diagnostics.extend(pending_cpu)
    durable, pending_durable = durable_recovery_payloads(ROOT)
    payloads.update(durable)
    supplemental.extend(sorted(durable))
    pending_diagnostics.extend(pending_durable)
    versioned_billing = billing_v2_payloads(ROOT)
    payloads.update(versioned_billing)
    supplemental.extend(sorted(versioned_billing))
    interruption = funding_interruption_payloads(ROOT)
    payloads.update(interruption)
    supplemental.extend(sorted(interruption))
    with tempfile.TemporaryDirectory() as directory:
        for name in (
            "runs/v2/spend.sqlite",
            "runs/v2/release/spend.sqlite",
            "runs/v2/release-r2/spend.sqlite",
        ):
            target = Path(directory) / Path(name).name
            # Use distinct targets and SQLite backup, not a possibly torn filesystem copy.
            target = target.with_name(name.replace("/", "-"))
            with sqlite3.connect(f"file:{ROOT / name}?mode=ro", uri=True) as source:
                with sqlite3.connect(target) as dest:
                    source.backup(dest)
            payloads[name] = target.read_bytes()
    # Credentials are read only to reject an accidental inclusion, never exported or logged.
    secrets = []
    for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            if not re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.IGNORECASE):
                continue
            value = value.strip().strip('"').strip("'")
            if len(value) >= 12:
                secrets.append(value.encode())
    reject_credentials(payloads, secrets)
    restore_sources = list(dict.fromkeys(g["source_archive"] for g in gates.values()))
    restore_sources.extend(
        path
        for path in dict.fromkeys(
            g["operational_amendment"]["source_archive"]
            for g in gates.values()
            if g.get("operational_amendment")
        )
        if path not in restore_sources
    )
    restore_sources.extend(
        path
        for path in dict.fromkeys(
            g["funding_operational_amendment"]["source_archive"]
            for g in gates.values()
            if g.get("funding_operational_amendment")
        )
        if path not in restore_sources
    )
    evidence = dict(
        batches=batches,
        protocol_hashes=sorted({g["protocol_hash"] for g in gates.values()}),
        restore="Extract this evidence archive into a clean directory, then extract each "
        "archive in restore_sources into that same directory in the listed order, allowing "
        "the operational amendment to replace driver files. Run python "
        "scripts/audit_release_v2.py --batch <batch>. No API call is needed.",
        restore_sources=restore_sources,
        supplemental_unranked_expense_evidence=supplemental,
        supplemental_pending_attempts_excluded=pending_diagnostics,
        credential_scan_passed=True,
        sha256={name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()},
    )
    payloads["EVIDENCE.json"] = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(payloads.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    with zipfile.ZipFile(output) as archive:
        for name, expected in evidence["sha256"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("export verification mismatch")
    return dict(
        path=str(output),
        bytes=output.stat().st_size,
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        batches=batches,
        credential_scan_passed=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.batch, args.output), indent=2))

#!/usr/bin/env python3
"""Offline by default: CPU-only scripted test of durable Sandbox artifact transport."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import math
import os
import random
import sqlite3
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "durable_transport", ROOT / "scripts/phonellm_durable_recovery.py"
)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
APP = "kitchenrush-cpu-transport-selftest-v1"
BOOTSTRAP = d.BOOTSTRAP.replace("phonellm_durable_recovery.py", "phonellm_cpu_transport_check.py")
RATE = 0.00003942 + 0.00000667


def fixture_padding():
    # Transport-only bytes never enter a model payload, kitchen, or token counter.
    return random.Random(20260917).randbytes(3 * 1024 * 1024)


def bundle():
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(d.source_bundle())) as old,
        zipfile.ZipFile(output, "w") as archive,
    ):
        for info in old.infolist():
            archive.writestr(info, old.read(info.filename))
        info = zipfile.ZipInfo("scripts/phonellm_cpu_transport_check.py")
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, Path(__file__).read_bytes())
    return output.getvalue()


def plan():
    old = d.plan()
    return dict(
        kind="transport_selftest",
        synthetic_responses=True,
        inference_executed=False,
        model_capability_result=False,
        rank_eligible=False,
        b80_eligible=False,
        protocol_hash=old["protocol_hash"],
        jobs=old["jobs"],
        token_cap=8192,
        max_decisions=160,
        scheduled_kitchens=2,
        app_name=APP,
        gpu=None,
        cpu_request_and_limit=1,
        memory_mib_request_and_limit=1024,
        lifetime_seconds=120,
        existing_image_id=d.guard.EXISTING_IMAGE_ID,
        source_archive_sha256=d.sha(bundle()),
        worker_sha256=d.sha(BOOTSTRAP.encode()),
        source_transport="stdin-zip-sha256-v1",
        output_transport="filesystem-zip-ack-v1",
        base_compute_envelope_usd=round(RATE * 120, 9),
        maximum_reservation_usd=0.25,
        price_source="https://modal.com/pricing",
        price_checked_date="2026-09-17",
        new_image_build=False,
        persistent_volumes=False,
        public_ports=False,
        resource_envelope_is_invoice_guarantee=False,
        transport_fixture_bytes=3 * 1024 * 1024,
        transport_fixture_sha256=d.sha(fixture_padding()),
        transport_fixture_note="Deterministic incompressible transport padding; never model input.",
    )


def command(planned):
    args = ("python", "-c", BOOTSTRAP, json.dumps(planned))
    d.require(sum(len(a.encode()) for a in args) <= 65536, "command size")
    return args


def check_receipt(receipt, planned, now=None):
    now = time.time() if now is None else now
    amount = float(receipt["reserved_usd"])
    d.require(
        math.isfinite(amount) and max(0.01, planned["base_compute_envelope_usd"]) <= amount <= 0.25,
        "CPU transport reservation must be between envelope floor and $0.25",
    )
    d.require(receipt["plan_sha256"] == d.guard.sha(planned), "receipt plan mismatch")
    d.require(
        receipt["reservation_id"].startswith("phonellm-cpu-transport-"),
        "distinct CPU reservation required",
    )
    d.require(0 <= now - float(receipt["verified_at_unix"]) <= 900, "stale resource evidence")
    d.require(now + 120 < 1790726400, "September envelope expired")
    d.require(receipt["resource_envelope_verified"] is True, "resource review required")
    d.require(receipt["existing_image_id"] == d.guard.EXISTING_IMAGE_ID, "image mismatch")
    evidence = Path(receipt["price_evidence_path"])
    d.require(
        d.sha(evidence.read_bytes()) == receipt["price_evidence_sha256"], "price evidence mismatch"
    )
    ledger = Path(receipt["ledger_path"]).resolve()
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT reserved,actual,status FROM requests WHERE id=?", (receipt["reservation_id"],)
        ).fetchone()
        cap = db.execute("SELECT cap FROM meta WHERE id=1").fetchone()[0]
        opening = db.execute("SELECT amount FROM opening WHERE id=1").fetchone()[0]
        used = db.execute(
            "SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM requests"
        ).fetchone()[0]
    d.require(
        row == (round(amount * 1e9), None, "reserved") and used + opening <= cap <= 200e9,
        "missing CPU reservation or exceeded campaign budget",
    )
    return amount


def synthetic_response(payload):
    return dict(
        model=d.guard.MODEL,
        synthetic_fixture=True,
        usage=dict(prompt_tokens=100, completion_tokens=16, total_tokens=116),
        choices=[dict(finish_reason="stop", message=dict(content='{"action":"wait(20)"}'))],
    )


def make_artifacts(planned, root):
    d.require(
        planned["kind"] == "transport_selftest" and planned["inference_executed"] is False,
        "scripted-only worker",
    )
    journal = d.Journal(root, planned)
    (root / "transport-fixture-padding.bin").write_bytes(fixture_padding())
    journal.emit(
        "runtime_verified",
        device="CPU",
        inference_executed=False,
        responses="scripted wait actions",
    )
    for job in planned["jobs"]:
        ep = d.original.run_one(planned, job, synthetic_response, journal.emit)
        d.require(ep["status"] == "complete", "fixture incomplete")
    return journal.finish()


def worker(planned):
    started = time.monotonic()
    root = Path(d.REMOTE)
    ready = make_artifacts(planned, root)
    print(json.dumps(dict(event="transport_artifact_ready", synthetic=True)), flush=True)
    deadline = min(started + 105, time.monotonic() + 60)
    while time.monotonic() < deadline:
        ack = root / "ACK.json"
        if ack.exists():
            try:
                if json.loads(ack.read_text()) == dict(
                    archive_sha256=ready["archive_sha256"], verified=True
                ):
                    return 0
            except (OSError, ValueError):
                pass
        time.sleep(0.25)
    return 1


def launch(planned, receipt_path, out):
    d.require(planned == plan(), "plan changed")
    args = command(planned)
    source = bundle()
    d.require(len(source) <= 2097152, "source size")
    d.require(d.sha(source) == planned["source_archive_sha256"], "source changed before dispatch")
    receipt = json.loads(receipt_path.read_text())
    amount = check_receipt(receipt, planned)
    claim = Path(receipt["ledger_path"]).resolve().parent / (
        ".phonellm-probe-" + d.guard.sha(receipt["reservation_id"])[:24] + ".claimed"
    )
    with claim.open("x") as f:
        f.write(json.dumps(dict(plan_sha256=d.guard.sha(planned), claimed_at_unix=time.time())))
        f.flush()
        os.fsync(f.fileno())
    import modal
    from modal.exception import SandboxFilesystemNotFoundError

    d.require(modal.__version__ == "1.5.2", "SDK version changed")

    workspace = modal.Workspace.from_context()
    workspace.hydrate()
    d.require(workspace.name == receipt["workspace"], "workspace mismatch")
    out.mkdir(parents=True, exist_ok=False)
    (out / "source.zip").write_bytes(source)
    d.original.save(out / "plan.json", planned)
    result = dict(
        kind="transport_selftest",
        inference_executed=False,
        synthetic_responses=True,
        plan_sha256=d.guard.sha(planned),
        receipt_sha256=d.sha(receipt_path.read_bytes()),
        reservation_id=receipt["reservation_id"],
        sdk=modal.__version__,
        app_name=APP,
        reserved_usd=amount,
        known_total_cost_usd=None,
        cost_status="unreconciled; retain reserve",
        rank_eligible=False,
        b80_eligible=False,
        verified=False,
        local_before_create_unix=time.time(),
    )
    sandbox = None
    d.original.save(out / "result.json", result)
    try:
        app = modal.App.lookup(APP, create_if_missing=True)
        sandbox = modal.Sandbox.create(
            *args,
            app=app,
            name="cpu-" + d.guard.sha(receipt["reservation_id"])[:20],
            tags={"purpose": "cpu-transport-selftest", "experiment": d.guard.sha(planned)[:20]},
            image=modal.Image.from_id(receipt["existing_image_id"]),
            gpu=None,
            cpu=(1, 1),
            memory=(1024, 1024),
            timeout=120,
            secrets=[],
            volumes={},
            encrypted_ports=[],
            unencrypted_ports=[],
        )
        result.update(sandbox_id=sandbox.object_id, local_after_create_unix=time.time())
        d.original.save(out / "result.json", result)
        sandbox.stdin.write(source)
        sandbox.stdin.write_eof()
        sandbox.stdin.drain()
        transfer = d.collect(
            sandbox, planned, out, SandboxFilesystemNotFoundError, time.monotonic() + 90
        )
        result["verified_synthetic_fixture_ids"] = transfer["verified_recovery_episodes"]
        sandbox.wait()
        result["exit_code"] = sandbox.returncode
        d.require(sandbox.returncode == 0, "worker exit")
        result["verified"] = True
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
    finally:
        result["local_before_terminate_unix"] = time.time()
        if sandbox is not None:
            try:
                sandbox.terminate(wait=True)
                result["termination_confirmed"] = True
            except Exception as exc:
                result["termination_error_type"] = type(exc).__name__
        result["local_after_terminate_unix"] = time.time()
        result["verified"] = result["verified"] and result.get("termination_confirmed") is True
        d.original.save(out / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    planned = plan()
    if not args.launch:
        print(json.dumps(dict(plan_sha256=d.guard.sha(planned), plan=planned), indent=2))
        return 0
    if args.receipt is None or args.out is None:
        parser.error("launch requires new receipt and output")
    result = launch(planned, args.receipt, args.out)
    print(json.dumps(dict(artifact=str(args.out / "result.json"), verified=result["verified"])))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline-by-default PhoneLLM compatibility probe; never a scored release runner.

No image builds, public ports, provider keys, persistent volumes, retry loop, or
campaign ledger writes. Launch requires an existing reserved ledger row and
independently verified price/image provenance evidence. See usage notes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MODEL = "pipecat-ai/phonellm-alpha-1"
REVISION = "8e76aaa6e8ce4765ac943ba3fb339494d4d48dca"
IMAGE = "lmsysorg/sglang:v0.5.18-cu130"
EXISTING_IMAGE_ID = "im-KNuhx8x5CNGSM9Kp9F9Xw8"
LIFETIME = 1800
CPU = 4
MEMORY_MIB = 65536
RATE = 0.001736 + CPU * 0.00003942 + (MEMORY_MIB / 1024) * 0.00000667

# Runs only inside the single Sandbox, using loopback HTTP. stdout is an allowlisted
# event stream. SGLang logs and raw provider output are never copied into artifacts.
WORKER = r"""
import importlib.metadata, json, os, subprocess, sys, time, urllib.error, urllib.request
p = json.loads(sys.argv[1])
started = time.monotonic()
def emit(event, **values):
    print(json.dumps(dict(event=event, elapsed_s=time.monotonic()-started,
                          utc_s=time.time(), **values)), flush=True)
emit("worker_started")
cmd = ["python", "-m", "sglang.launch_server", "--model-path", p["model"],
       "--revision", p["revision"], "--served-model-name", p["model"],
       "--host", "127.0.0.1", "--port", "8000", "--trust-remote-code",
       "--tool-call-parser", "qwen3_coder", "--reasoning-parser", "nemotron_3",
       "--context-length", "32768", "--max-running-requests", "1",
       "--cuda-graph-max-bs", "8"]
server = None
try:
    version = importlib.metadata.version("sglang")
    if version != "0.5.18":
        raise RuntimeError("sglang_version_mismatch")
    import torch
    gpu_name = torch.cuda.get_device_name(0)
    if "B200" not in gpu_name or torch.cuda.device_count() != 1:
        raise RuntimeError("gpu_mismatch")
    emit("runtime_verified", sglang_version=version, torch_version=torch.__version__,
         cuda_version=torch.version.cuda, gpu=gpu_name, model_revision=p["revision"])
    server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ready = False
    while time.monotonic()-started < 900:
        if server.poll() is not None:
            raise RuntimeError("server_exit")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as r:
                ready = r.status == 200
        except (OSError, urllib.error.URLError):
            pass
        if ready:
            break
        time.sleep(2)
    if not ready:
        raise TimeoutError("readiness_timeout")
    emit("server_ready")
    # No separate warmup: the first attempted request includes cold inference work.
    for index, payload in enumerate(p["requests"]):
        emit("request_started", index=index)
        req = urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions",
              data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=240) as r:
            raw = r.read(1048577)
            if len(raw)>1048576:
                raise ValueError("response_size")
            answer = json.loads(raw)
        choice = answer.get("choices", [{}])[0]
        try:
            obj = json.loads(choice.get("message", {}).get("content", ""))
        except (ValueError, TypeError):
            obj = None
        domain = payload["response_format"]["json_schema"]["schema"]["properties"]["action"]["enum"]
        valid = (choice.get("finish_reason")=="stop" and isinstance(obj, dict)
                 and set(obj)=={"action"} and obj["action"] in domain
                 and answer.get("model")==p["model"])
        usage = answer.get("usage", {})
        usage = {k:v for k,v in usage.items() if k in
                 ("prompt_tokens","completion_tokens","total_tokens") and type(v)==int and v>=0}
        emit("request_finished", index=index, syntactic_action_valid=valid,
             action=obj["action"] if valid else None, usage=usage,
             finish_reason=choice.get("finish_reason") if choice.get("finish_reason")
             in ("stop","length","content_filter","tool_calls") else "other")
        if not valid:
            break
except Exception as exc:
    known_errors = {"sglang_version_mismatch", "gpu_mismatch", "server_exit",
                    "readiness_timeout", "response_size"}
    emit("probe_error", error_type=type(exc).__name__,
         reason=str(exc) if str(exc) in known_errors else "unclassified")
finally:
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    emit("worker_finished")
"""


def sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def plan():
    from kitchenrush.v2.environment import Engine, decision, generate, protocol_hash, public_state
    from kitchenrush.v2.providers import Client, Model

    client = Client(Model("probe", MODEL, "local", 0, 0, max_output=16384))
    requests = []
    for seed in (0, 1, 2):
        canonical, _ = decision(
            public_state(Engine(generate(seed, "development", 3))),
            {"view": "unranked_compatibility_probe", "remaining_decisions": 1},
        )
        payload = client.payload(canonical)
        payload.pop("provider")
        payload["temperature"] = 0
        payload["chat_template_kwargs"] = {"enable_thinking": False}
        requests.append(payload)
    return {
        "kind": "unranked_phonellm_compatibility_probe",
        "protocol_hash": protocol_hash(),
        "model": MODEL,
        "revision": REVISION,
        "image_registry_tag": IMAGE,
        "existing_image_id": EXISTING_IMAGE_ID,
        "image_provenance": "Immutable image ID from deployed PhoneLLM app layout; "
        "historical registry tag inferred from local source, runtime version checked before load",
        "gpu": "B200",
        "cpu_request_and_limit": CPU,
        "memory_mib_request_and_limit": MEMORY_MIB,
        "lifetime_seconds": LIFETIME,
        "base_compute_envelope_usd": round(RATE * LIFETIME, 9),
        "base_resource_rate_usd_s": RATE,
        "price_source": "https://modal.com/pricing",
        "price_checked_date": "2026-09-17",
        "reservation_floor_usd": 5.0,
        "egress_free_until_utc": "2026-10-01T00:00:00+00:00",
        "egress_source": "https://modal.com/docs/guide/network-egress-billing",
        "resource_envelope_basis": "September resources; existing image, no volumes or region pin",
        "reserved_amount_is_invoice_guarantee": False,
        "region": None,
        "persistent_volumes": False,
        "public_ports": False,
        "worker_sha256": hashlib.sha256(WORKER.encode()).hexdigest(),
        "requests": requests,
    }


def check_receipt(receipt, planned, *, now=None):
    """Fail closed; external evidence review is separate from read-only ledger checks."""
    now = time.time() if now is None else now
    if receipt["plan_sha256"] != sha(planned):
        raise ValueError("receipt plan hash mismatch")
    envelope = float(receipt["reserved_usd"])
    if not math.isfinite(envelope) or not 5 <= envelope <= 10:
        raise ValueError("require a $5 to $10 reserved session envelope")
    # Leave a full day for scheduling/lifecycle uncertainty before free egress ends.
    expiry = datetime.fromisoformat("2026-09-30T00:00:00+00:00").timestamp()
    if now + LIFETIME >= expiry:
        raise ValueError("September envelope expired; re-audit egress before launch")
    if not 0 <= now - float(receipt["verified_at_unix"]) <= 900:
        raise ValueError("price/image evidence must be reverified within 15 minutes")
    if receipt["resource_envelope_verified"] is not True:
        raise ValueError("resource-envelope review required")
    if receipt["existing_image_id"] != EXISTING_IMAGE_ID:
        raise ValueError("existing image provenance mismatch")
    for kind in ("price",):
        evidence = Path(receipt[kind + "_evidence_path"])
        if hashlib.sha256(evidence.read_bytes()).hexdigest() != receipt[kind + "_evidence_sha256"]:
            raise ValueError("evidence file hash mismatch")
    ledger = Path(receipt["ledger_path"]).resolve()
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as con:
        row = con.execute(
            "SELECT reserved,actual,status FROM requests WHERE id=?", (receipt["reservation_id"],)
        ).fetchone()
        cap = con.execute("SELECT cap FROM meta WHERE id=1").fetchone()[0]
        opening = con.execute("SELECT amount FROM opening WHERE id=1").fetchone()[0]
        used = con.execute(
            "SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM requests"
        ).fetchone()[0]
    if row != (round(envelope * 1e9), None, "reserved") or used + opening > cap or cap > 200e9:
        raise ValueError("missing matching live campaign reservation or invalid campaign ceiling")
    return envelope


def launch(planned, receipt_path, out):
    receipt = json.loads(receipt_path.read_text())
    envelope = check_receipt(receipt, planned)
    # One-use marker is keyed by reserved ledger row, even if someone copies the receipt.
    # Retain after EVERY failure. No resume or automatic sandbox recreation.
    claim = Path(receipt["ledger_path"]).resolve().parent / (
        ".phonellm-probe-" + sha(receipt["reservation_id"])[:24] + ".claimed"
    )
    with claim.open("x") as f:
        f.write(json.dumps({"plan_sha256": sha(planned), "claimed_at_unix": time.time()}))
        f.flush()
        os.fsync(f.fileno())
    import modal

    workspace = modal.Workspace.from_context()
    workspace.hydrate()
    if workspace.name != receipt["workspace"]:
        raise ValueError("active Modal workspace differs from reservation evidence")
    out.mkdir(parents=True, exist_ok=False)
    (out / "plan.json").write_text(json.dumps(planned, indent=2) + "\n")
    result = {
        "rank_eligible": False,
        "plan_sha256": sha(planned),
        "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "reserved_usd": envelope,
        "cost_status": "unreconciled; retain full reserve",
        "resource_enforcement_basis": "provider resource/lifetime limits; no account cap assumed",
        "reserved_amount_is_invoice_guarantee": False,
        "sdk": modal.__version__,
        "events": [],
    }

    def save():
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    sandbox = None
    result["local_before_create_unix"] = time.time()
    save()
    try:
        # Loading an EXISTING image avoids a new image build outside the lifetime bound.
        # App metadata creation is not a second compute container.
        app = modal.App.lookup("kitchenrush-phonellm-bounded-probe", create_if_missing=True)
        sandbox = modal.Sandbox.create(
            "python",
            "-c",
            WORKER,
            json.dumps(planned),
            app=app,
            name="probe-" + sha(receipt["reservation_id"])[:20],
            tags={
                "purpose": "phonellm-unranked-probe",
                "probe": sha(receipt["reservation_id"])[:20],
            },
            image=modal.Image.from_id(receipt["existing_image_id"]),
            gpu="B200",
            cpu=(CPU, CPU),
            memory=(MEMORY_MIB, MEMORY_MIB),
            timeout=LIFETIME,
            env={"HF_HOME": "/tmp/huggingface", "HF_XET_HIGH_PERFORMANCE": "1"},
            secrets=[],
            volumes={},
            encrypted_ports=[],
            unencrypted_ports=[],
        )
        result["sandbox_id"] = sandbox.object_id
        result["local_after_create_unix"] = time.time()
        save()
        for line in sandbox.stdout:
            # Only the worker emits stdout; discard unexpected lines and excess events.
            if len(line) > 8192 or len(result["events"]) >= 20:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("event") in {
                "worker_started",
                "runtime_verified",
                "server_ready",
                "request_started",
                "request_finished",
                "probe_error",
                "worker_finished",
            }:
                result["events"].append(event)
                save()
        sandbox.wait()
        result["exit_code"] = sandbox.returncode
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        # No exception strings: they can include transport endpoints or credentials.
    finally:
        result["local_before_terminate_unix"] = time.time()
        save()
        if sandbox is not None:
            try:
                sandbox.terminate(wait=True)
                result["termination_confirmed"] = True
            except Exception as exc:
                result["termination_error_type"] = type(exc).__name__
        result["local_after_terminate_unix"] = time.time()
        result["full_session_wall_estimate_usd"] = RATE * (
            result["local_after_terminate_unix"] - result["local_before_create_unix"]
        )
        result["wall_estimate_note"] = (
            "Diagnostic only: includes scheduling/startup/requests/idle/shutdown; "
            "not provider billing boundaries, not an action admission meter. "
            "No warmup/shared overhead is excluded. Retain full reservation until reconciliation."
        )
        save()
    print(json.dumps({"artifact": str(out / "result.json"), "rank_eligible": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true", help="PAID; requires validated receipt")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "runs/phonellm-probe")
    args = parser.parse_args()
    planned = plan()
    if not args.launch:
        print(json.dumps({"plan_sha256": sha(planned), "plan": planned}, indent=2))
        return
    if args.receipt is None:
        parser.error("--launch requires --receipt; no launch performed")
    launch(planned, args.receipt.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()

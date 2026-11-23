#!/usr/bin/env python3
"""Offline-by-default full-task PhoneLLM diagnostic. No dollar admission or release ranking."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import sys
import time
import zipfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2.environment import (  # noqa: E402
    Engine,
    decision,
    digest,
    generate,
    protocol_hash,
    public_state,
    quality,
    scripted_episode,
    serialize_spec,
)
from kitchenrush.v2.providers import Client, Model  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "phone_probe_guard", ROOT / "scripts/probe_phonellm_sandbox.py"
)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

BOOTSTRAP = """import hashlib,io,json,runpy,sys,tempfile,zipfile
p=json.loads(sys.argv[1])
b=sys.stdin.buffer.read(2097153)
if len(b)>2097152 or hashlib.sha256(b).hexdigest()!=p['source_archive_sha256']:
 raise ValueError('source_transfer_integrity')
with tempfile.TemporaryDirectory() as d:
 z=zipfile.ZipFile(io.BytesIO(b))
 z.extractall(d)
 runpy.run_path(d+'/scripts/phonellm_episode_diagnostic.py',run_name='phone_worker')['worker'](p)
"""


def launch_args(planned):
    args = ("python", "-c", BOOTSTRAP, json.dumps(planned))
    # Modal 1.5.2 validates the TOTAL CMD, not a per-argument Linux limit.
    if sum(len(arg.encode()) for arg in args) > 65536:
        raise ValueError("sandbox_cmd_too_large")
    return args


def bundle():
    output = io.BytesIO()
    paths = sorted((ROOT / "src").rglob("*.py")) + [
        Path(__file__),
        ROOT / "scripts/probe_phonellm_sandbox.py",
    ]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return output.getvalue()


def plan():
    value = guard.plan()
    value.pop("requests")
    value.update(
        kind="unranked_phonellm_full_episode_diagnostic",
        token_cap=8192,
        max_decisions=160,
        scheduled_kitchens=3,
        rank_eligible=False,
        b80_eligible=False,
        dollar_admission=False,
        source_transport="stdin-zip-sha256-v1",
        worker_sha256=hashlib.sha256(BOOTSTRAP.encode()).hexdigest(),
        source_archive_sha256=hashlib.sha256(bundle()).hexdigest(),
        guard_source_sha256=hashlib.sha256(
            (ROOT / "scripts/probe_phonellm_sandbox.py").read_bytes()
        ).hexdigest(),
        jobs=[
            dict(
                id=f"phonellm-development-{size}-seed{74000 + size}-t8192",
                seed=74000 + size,
                size=size,
                split="development",
            )
            for size in (2, 3, 4)
        ],
        cost_allocation=(
            "After gross billing reconciliation: entire dedicated session cost / 3 "
            "scheduled kitchens; include cold startup, failed work, idle and shutdown. "
            "No successful-only denominator or per-action dollar allocation."
        ),
    )
    return value


def model():
    return Model("phonellm-alpha-1-diagnostic", guard.MODEL, "local", 0, 0, max_output=16384)


def payload_for(canonical, remaining):
    payload = Client(model()).payload(canonical, min(16384, remaining))
    payload.pop("provider")
    payload.update(temperature=0, chat_template_kwargs={"enable_thinking": False})
    return payload


def response_usage(response, maximum):
    if response.get("model") != guard.MODEL:
        raise ValueError("model_identity")
    usage = response["usage"]
    ni, no = usage["prompt_tokens"], usage["completion_tokens"]
    if any(type(n) is not int or n < 0 for n in (ni, no)) or no > maximum or ni > 32768:
        raise ValueError("token_accounting")
    if "total_tokens" in usage and usage["total_tokens"] != ni + no:
        raise ValueError("token_total")
    return ni, no


def run_one(planned, job, evaluate, emit):
    spec = generate(job["seed"], job["split"], job["size"])
    witness = scripted_episode(spec)
    if witness["quality"] != 100 or witness["invalid"]:
        raise ValueError("infeasible_task")
    engine = Engine(spec, record_trace=True)
    ep = dict(
        id=job["id"],
        status="running",
        protocol_hash=planned["protocol_hash"],
        spec=serialize_spec(spec),
        spec_hash=digest(serialize_spec(spec)),
        feasibility_witness_hash=digest(witness["actions"]),
        model=guard.MODEL,
        revision=guard.REVISION,
        token_cap=8192,
        max_decisions=160,
        input_tokens=0,
        output_tokens=0,
        cost_usd=None,
        requests=[],
        actions=[],
        rank_eligible=False,
        b80_eligible=False,
    )
    emit("episode_started", episode=ep)
    try:
        while not engine.terminated and len(ep["actions"]) < 160 and ep["output_tokens"] < 8192:
            remaining = 8192 - ep["output_tokens"]
            canonical, options = decision(
                public_state(engine),
                dict(
                    kind="generation_tokens",
                    allowance=8192,
                    remaining=remaining,
                    decisions_remaining=160 - len(ep["actions"]),
                ),
            )
            payload = payload_for(canonical, remaining)
            record = dict(
                index=len(ep["requests"]), canonical=canonical, payload=payload, admitted=False
            )
            ep["requests"].append(record)
            emit("request_started", episode_id=ep["id"], record=record)
            response = evaluate(payload)
            record["response"] = response
            emit("response_received", episode_id=ep["id"], record=record)
            ni, no = response_usage(response, payload["max_tokens"])
            ep["input_tokens"] += ni
            ep["output_tokens"] += no
            selection = Client(model()).select(response)
            call = options.get(selection) if isinstance(selection, str) else None
            call = call or ToolCall("invalid_model_selection", {})
            engine.step([call])
            ep["actions"].append(asdict(call))
            record.update(
                input_tokens=ni,
                output_tokens=no,
                selection=selection,
                admitted=True,
                post_state_hash=digest(public_state(engine)),
                post_quality=quality(engine),
            )
            emit("request_finished", episode_id=ep["id"], record=record, action=asdict(call))
        ep.update(
            status="complete",
            reason="game_terminal"
            if engine.terminated
            else "token_budget_exhausted"
            if ep["output_tokens"] == 8192
            else "decision_limit",
        )
    except Exception as exc:
        ep.update(
            status="quarantined",
            reason="provider_or_accounting_failure",
            error_type=type(exc).__name__,
        )
    ep.update(
        quality=quality(engine),
        completed=engine.counters["orders_served"],
        invalid=engine.counters["invalid_actions"],
        frames=engine.trace,
    )
    emit("episode_finished", episode=ep)
    return ep


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_episode(ep, planned, job):
    """Reconstruct every public request, provider payload, choice, and deterministic transition."""
    require(
        ep["status"] == "complete" and ep["id"] == job["id"],
        "diagnostic verification failed at check 208",
    )
    require(
        planned["protocol_hash"] == protocol_hash() == ep["protocol_hash"],
        "diagnostic verification failed at check 209",
    )
    require(
        ep["model"] == guard.MODEL and ep["revision"] == guard.REVISION,
        "diagnostic verification failed at check 210",
    )
    require(
        ep["token_cap"] == 8192 and ep["max_decisions"] == 160 and (ep["cost_usd"] is None),
        "diagnostic verification failed at check 211",
    )
    spec = generate(job["seed"], job["split"], job["size"])
    require(
        ep["spec"] == serialize_spec(spec) and ep["spec_hash"] == digest(ep["spec"]),
        "diagnostic verification failed at check 213",
    )
    require(
        ep["feasibility_witness_hash"] == digest(scripted_episode(spec)["actions"]),
        "diagnostic verification failed at check 214",
    )
    engine = Engine(spec, record_trace=True)
    ni_total = no_total = 0
    require(
        len(ep["actions"]) == len(ep["requests"]) <= 160,
        "diagnostic verification failed at check 217",
    )
    for index, record in enumerate(ep["requests"]):
        require(
            not engine.terminated and no_total < 8192, "diagnostic verification failed at check 219"
        )
        (canonical, options) = decision(
            public_state(engine),
            dict(
                kind="generation_tokens",
                allowance=8192,
                remaining=8192 - no_total,
                decisions_remaining=160 - index,
            ),
        )
        require(record["canonical"] == canonical, "diagnostic verification failed at check 229")
        expected = payload_for(canonical, 8192 - no_total)
        require(record["payload"] == expected, "diagnostic verification failed at check 231")
        (ni, no) = response_usage(record["response"], expected["max_tokens"])
        ni_total += ni
        no_total += no
        require(
            (record["input_tokens"], record["output_tokens"]) == (ni, no),
            "diagnostic verification failed at check 235",
        )
        selection = Client(model()).select(record["response"])
        require(
            record["selection"] == selection and record["admitted"] is True,
            "diagnostic verification failed at check 237",
        )
        call = options.get(selection) if isinstance(selection, str) else None
        call = call or ToolCall("invalid_model_selection", {})
        require(ep["actions"][index] == asdict(call), "diagnostic verification failed at check 240")
        engine.step([call])
        require(
            record["post_state_hash"] == digest(public_state(engine))
            and record["post_quality"] == quality(engine),
            "diagnostic verification failed at check 242",
        )
    require(
        ep["input_tokens"] == ni_total and ep["output_tokens"] == no_total,
        "diagnostic verification failed at check 245",
    )
    require(
        engine.terminated or no_total == 8192 or len(ep["actions"]) == 160,
        "diagnostic verification failed at check 246",
    )
    reason = (
        "game_terminal"
        if engine.terminated
        else "token_budget_exhausted"
        if no_total == 8192
        else "decision_limit"
    )
    require(
        ep["reason"] == reason and ep["quality"] == quality(engine),
        "diagnostic verification failed at check 254",
    )
    require(
        ep["completed"] == engine.counters["orders_served"]
        and ep["invalid"] == engine.counters["invalid_actions"],
        "diagnostic verification failed at check 255",
    )
    require(
        digest(ep["frames"]) == digest(engine.trace), "diagnostic verification failed at check 259"
    )
    return True


def worker(planned):
    import importlib.metadata
    import subprocess
    import urllib.request

    import torch

    started = time.monotonic()

    def emit(event, **values):
        print(
            json.dumps(
                dict(
                    event=event, utc_s=time.time(), elapsed_s=time.monotonic() - started, **values
                ),
                separators=(",", ":"),
            ),
            flush=True,
        )

    server = None
    emit("worker_started")
    try:
        if (
            protocol_hash() != planned["protocol_hash"]
            or importlib.metadata.version("sglang") != "0.5.18"
        ):
            raise ValueError("runtime_mismatch")
        if torch.cuda.device_count() != 1 or "B200" not in torch.cuda.get_device_name(0):
            raise ValueError("gpu_mismatch")
        emit(
            "runtime_verified",
            sglang_version="0.5.18",
            gpu=torch.cuda.get_device_name(0),
            revision=guard.REVISION,
        )
        cmd = [
            "python",
            "-m",
            "sglang.launch_server",
            "--model-path",
            guard.MODEL,
            "--revision",
            guard.REVISION,
            "--served-model-name",
            guard.MODEL,
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--trust-remote-code",
            "--tool-call-parser",
            "qwen3_coder",
            "--reasoning-parser",
            "nemotron_3",
            "--context-length",
            "32768",
            "--max-running-requests",
            "1",
            "--cuda-graph-max-bs",
            "8",
        ]
        server = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while True:
            if server.poll() is not None or time.monotonic() - started > 900:
                raise RuntimeError("readiness_failure")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            time.sleep(2)
        emit("server_ready")

        def evaluate(payload):
            request = urllib.request.Request(
                "http://127.0.0.1:8000/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=240) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise ValueError("response_size")
                return json.loads(raw)

        for job in planned["jobs"]:
            if run_one(planned, job, evaluate, emit)["status"] != "complete":
                break
    except Exception as exc:
        emit("worker_error", error_type=type(exc).__name__)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        emit("worker_finished")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def launch(planned, receipt_path, out):
    if planned != plan():
        raise ValueError("plan_changed")
    args = launch_args(planned)
    receipt = json.loads(receipt_path.read_text())
    reserve = guard.check_receipt(receipt, planned)
    source = bundle()
    if len(source) > 2097152:
        raise ValueError("source_transfer_too_large")
    if hashlib.sha256(source).hexdigest() != planned["source_archive_sha256"]:
        raise ValueError("source_changed")
    claim = Path(receipt["ledger_path"]).resolve().parent / (
        ".phonellm-probe-" + guard.sha(receipt["reservation_id"])[:24] + ".claimed"
    )
    with claim.open("x") as file:
        file.write(json.dumps(dict(plan_sha256=guard.sha(planned), claimed_at_unix=time.time())))
        file.flush()
        os.fsync(file.fileno())
    import modal

    workspace = modal.Workspace.from_context()
    workspace.hydrate()
    if workspace.name != receipt["workspace"]:
        raise ValueError("workspace_mismatch")
    out.mkdir(parents=True, exist_ok=False)
    (out / "source.zip").write_bytes(source)
    save(out / "plan.json", planned)
    result = dict(
        kind=planned["kind"],
        plan_sha256=guard.sha(planned),
        receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        reservation_id=receipt["reservation_id"],
        rank_eligible=False,
        b80_eligible=False,
        scheduled_kitchens=3,
        reserved_usd=reserve,
        known_total_cost_usd=None,
        cost_per_scheduled_kitchen_usd=None,
        cost_status="unreconciled; retain full reserve",
        sdk=modal.__version__,
        events=[],
        episodes={},
        local_before_create_unix=time.time(),
    )
    save(out / "result.json", result)
    sandbox = None
    artifacts = {}
    try:
        app = modal.App.lookup(
            "kitchenrush-phonellm-full-episode-diagnostic", create_if_missing=True
        )
        sandbox = modal.Sandbox.create(
            *args,
            app=app,
            name="episode-" + guard.sha(receipt["reservation_id"])[:20],
            tags={
                "purpose": "phonellm-unranked-full-episodes",
                "experiment": guard.sha(planned)[:20],
            },
            image=modal.Image.from_id(receipt["existing_image_id"]),
            gpu="B200",
            cpu=(4, 4),
            memory=(65536, 65536),
            timeout=1800,
            env={"HF_HOME": "/tmp/huggingface", "HF_XET_HIGH_PERFORMANCE": "1"},
            secrets=[],
            volumes={},
            encrypted_ports=[],
            unencrypted_ports=[],
        )
        result.update(sandbox_id=sandbox.object_id, local_after_create_unix=time.time())
        save(out / "result.json", result)
        sandbox.stdin.write(source)
        sandbox.stdin.write_eof()
        sandbox.stdin.drain()
        result["source_transfer_complete"] = True
        save(out / "result.json", result)
        for count, line in enumerate(sandbox.stdout):
            if count >= 1500 or len(line) > 16 * 1024 * 1024:
                raise ValueError("event_limit")
            event = json.loads(line)
            kind = event.get("event")
            if kind in ("episode_started", "episode_finished"):
                ep = event["episode"]
                eid = ep["id"]
                if eid not in {j["id"] for j in planned["jobs"]}:
                    raise ValueError("unexpected_episode")
                artifacts[eid] = ep
                save(out / "episodes" / (eid + ".json"), ep)
                result["episodes"][eid] = ep["status"]
            elif kind in ("request_started", "response_received", "request_finished"):
                eid = event["episode_id"]
                ep = artifacts[eid]
                record = event["record"]
                index = record["index"]
                if kind == "request_started":
                    if index != len(ep["requests"]):
                        raise ValueError("request_sequence")
                    ep["requests"].append(record)
                else:
                    if index != len(ep["requests"]) - 1:
                        raise ValueError("response_sequence")
                    ep["requests"][index] = record
                    if kind == "request_finished":
                        ep["actions"].append(event["action"])
                        ep["input_tokens"] += record["input_tokens"]
                        ep["output_tokens"] += record["output_tokens"]
                save(out / "episodes" / (eid + ".json"), ep)
            elif kind in (
                "worker_started",
                "runtime_verified",
                "server_ready",
                "worker_finished",
                "worker_error",
            ):
                result["events"].append(event)
            else:
                raise ValueError("unexpected_event")
            save(out / "result.json", result)
        sandbox.wait()
        result["exit_code"] = sandbox.returncode
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
    finally:
        result["local_before_terminate_unix"] = time.time()
        save(out / "result.json", result)
        if sandbox is not None:
            try:
                sandbox.terminate(wait=True)
                result["termination_confirmed"] = True
            except Exception as exc:
                result["termination_error_type"] = type(exc).__name__
        result["local_after_terminate_unix"] = time.time()
        result["full_session_wall_estimate_usd"] = guard.RATE * (
            result["local_after_terminate_unix"] - result["local_before_create_unix"]
        )
        result["wall_estimate_note"] = (
            "Diagnostic envelope only, not provider billing or action admission."
        )
        result["replay_verified_episodes"] = []
        for job in planned["jobs"]:
            ep = artifacts.get(job["id"])
            if ep and ep["status"] == "complete":
                try:
                    verify_episode(ep, planned, job)
                    result["replay_verified_episodes"].append(job["id"])
                except Exception as exc:
                    result.setdefault("verification_errors", {})[job["id"]] = type(exc).__name__
        result["runtime_verified"] = any(
            e.get("event") == "runtime_verified"
            and e.get("sglang_version") == "0.5.18"
            and "B200" in e.get("gpu", "")
            and e.get("revision") == guard.REVISION
            for e in result["events"]
        )
        result["all_three_complete_and_verified"] = (
            len(result["replay_verified_episodes"]) == 3
            and result["runtime_verified"]
            and result.get("termination_confirmed") is True
            and result.get("exit_code") == 0
            and "error_type" not in result
        )

        save(out / "result.json", result)
    print(json.dumps(dict(artifact=str(out / "result.json"), rank_eligible=False)))
    return result


def verify_saved(out):
    planned = json.loads((out / "plan.json").read_text())
    require(planned == plan(), "saved plan or diagnostic source differs; use archived source")
    require(
        hashlib.sha256((out / "source.zip").read_bytes()).hexdigest()
        == planned["source_archive_sha256"],
        "source archive differs",
    )
    expected = {job["id"] for job in planned["jobs"]}
    require(
        {p.stem for p in (out / "episodes").glob("*.json")} == expected,
        "missing or unexpected scheduled kitchen",
    )
    for job in planned["jobs"]:
        verify_episode(
            json.loads((out / "episodes" / (job["id"] + ".json")).read_text()), planned, job
        )
    return dict(
        status="three_episode_replays_verified",
        episodes=3,
        rank_eligible=False,
        b80_eligible=False,
        monetary_status="Replay verification does not establish billing or dollar admission.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "runs/v2/phonellm-full-episode-diagnostic"
    )
    args = parser.parse_args()
    planned = plan()
    if args.verify:
        if args.launch:
            parser.error("--verify cannot be combined with --launch")
        print(json.dumps(verify_saved(args.verify.resolve()), indent=2))
        return
    if not args.launch:
        print(json.dumps(dict(plan_sha256=guard.sha(planned), plan=planned), indent=2))
        return
    if args.receipt is None:
        parser.error("--launch requires reserved --receipt")
    launch(planned, args.receipt.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()

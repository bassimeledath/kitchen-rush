#!/usr/bin/env python3
"""Offline-first, unranked recovery of only PhoneLLM's two unfinished kitchens."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "original_phone_diagnostic", ROOT / "scripts/phonellm_episode_diagnostic.py"
)
original = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(original)
guard = original.guard
PRIOR = "runs/v2/phonellm-episodes-20260917/execution-attempt2"
APP = "kitchenrush-phonellm-durable-recovery-v1"
REMOTE = "/tmp/kitchenrush-durable"
BOOTSTRAP = """import hashlib,io,json,runpy,sys,tempfile,zipfile
p=json.loads(sys.argv[1]); b=sys.stdin.buffer.read(2097153)
if len(b)>2097152 or hashlib.sha256(b).hexdigest()!=p['source_archive_sha256']:
 raise ValueError('source_integrity')
with tempfile.TemporaryDirectory() as d:
 zipfile.ZipFile(io.BytesIO(b)).extractall(d)
 sys.exit(runpy.run_path(d+'/scripts/phonellm_durable_recovery.py',run_name='durable_worker')['worker'](p))
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def source_bundle():
    data = io.BytesIO()
    paths = sorted((ROOT / "src").rglob("*.py")) + [
        Path(__file__),
        ROOT / "scripts/phonellm_episode_diagnostic.py",
        ROOT / "scripts/probe_phonellm_sandbox.py",
    ]
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        for path in paths:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    return data.getvalue()


def plan():
    previous = ROOT / PRIOR
    old = json.loads((previous / "plan.json").read_text())
    result = json.loads((previous / "result.json").read_text())
    require(result.get("termination_confirmed") is True, "prior sandbox not terminated")
    require(
        sha((previous / "source.zip").read_bytes()) == old["source_archive_sha256"],
        "prior source archive changed",
    )
    require(result["plan_sha256"] == guard.sha(old), "prior result binding")
    completed_job = old["jobs"][0]
    completed_file = previous / "episodes" / (completed_job["id"] + ".json")
    completed = json.loads(completed_file.read_text())
    original.verify_episode(completed, old, completed_job)
    jobs = old["jobs"][1:]
    require([j["size"] for j in jobs] == [3, 4], "recovery job scope")
    prior_files = [previous / "plan.json", previous / "result.json", previous / "source.zip"]
    prior_files += sorted((previous / "episodes").glob("*.json"))
    usage = {}
    for p in (previous / "episodes").glob("*.json"):
        ep = json.loads(p.read_text())
        ni = no = 0
        for record in ep["requests"]:
            if "response" in record:
                i, o = original.response_usage(record["response"], record["payload"]["max_tokens"])
                ni += i
                no += o
        usage[ep["id"]] = dict(
            known_input_tokens=ni,
            known_output_tokens=no,
            unfinished_request_count=sum("response" not in r for r in ep["requests"]),
            unknown_unobserved_usage=ep["status"] != "complete",
        )
    p = dict(old)
    p.update(
        kind="unranked_phonellm_durable_recovery_v1",
        app_name=APP,
        jobs=jobs,
        source_transport="stdin-zip-sha256-v1",
        output_transport="filesystem-zip-ack-v1",
        source_archive_sha256=sha(source_bundle()),
        worker_sha256=sha(BOOTSTRAP.encode()),
        preserved_completed_job=dict(
            id=completed["id"],
            quality=completed["quality"],
            artifact=completed_file.relative_to(ROOT).as_posix(),
        ),
        prior_artifacts={f.relative_to(ROOT).as_posix(): sha(f.read_bytes()) for f in prior_files},
        prior_known_usage=usage,
        scheduled_kitchens=3,
        recovery_jobs=2,
        replacement_affected=True,
        rank_eligible=False,
        b80_eligible=False,
        accounting="Retain all previous prefix tokens and all session costs/liabilities. "
        "Each recovery attempt has 8192 tokens; cumulative job usage can exceed 8192. "
        "Allocate total campaign gross hosting cost over 3 originally scheduled kitchens.",
    )
    return p


def command(planned):
    args = ("python", "-c", BOOTSTRAP, json.dumps(planned))
    require(sum(len(a.encode()) for a in args) <= 65536, "command_size")
    return args


class Journal:
    def __init__(self, root, planned):
        self.root, self.planned, self.index = root, planned, 0
        original.save(root / "plan.json", planned)

    def emit(self, event, **values):
        # Persist full events atomically, never send their payload through stdout logs.
        original.save(
            self.root / "events" / f"{self.index:06d}.json",
            dict(event=event, utc_s=time.time(), **values),
        )
        self.index += 1
        if event in ("episode_started", "episode_finished"):
            ep = values["episode"]
            original.save(self.root / "episodes" / (ep["id"] + ".json"), ep)
        if event == "runtime_verified":
            original.save(self.root / "runtime.json", values)

    def finish(self, error=None):
        original.save(self.root / "status.json", dict(worker_error=error, events=self.index))
        payloads = {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in sorted(self.root.rglob("*.json"))
        }
        if self.planned.get("kind") == "transport_selftest":
            payloads["transport-fixture-padding.bin"] = (
                self.root / "transport-fixture-padding.bin"
            ).read_bytes()
        manifest = {n: sha(b) for n, b in payloads.items()}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            for n, b in payloads.items():
                z.writestr(n, b)
            z.writestr("MANIFEST.json", json.dumps(manifest))
        data = output.getvalue()
        temp = self.root / "artifacts.zip.tmp"
        temp.write_bytes(data)
        temp.replace(self.root / "artifacts.zip")
        ready = dict(
            plan_sha256=guard.sha(self.planned),
            archive_sha256=sha(data),
            archive_bytes=len(data),
            worker_error=error,
        )
        original.save(self.root / "READY.json", ready)
        return ready


def verify_archive(data, planned, ready):
    require(
        len(data) == ready["archive_bytes"] and sha(data) == ready["archive_sha256"],
        "artifact transfer checksum mismatch",
    )
    require(ready["plan_sha256"] == guard.sha(planned), "artifact plan mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        require(
            len(names) == len(set(names))
            and all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in names),
            "unsafe archive",
        )
        manifest = json.loads(z.read("MANIFEST.json"))
        require(set(names) == set(manifest) | {"MANIFEST.json"}, "artifact manifest mismatch")
        for n, digest in manifest.items():
            require(sha(z.read(n)) == digest, "artifact member checksum mismatch")
        require(json.loads(z.read("plan.json")) == planned, "artifact plan changed")
        status = json.loads(z.read("status.json"))
        require(status["worker_error"] is None and ready["worker_error"] is None, "worker failed")
        runtime = json.loads(z.read("runtime.json"))
        if planned.get("kind") == "transport_selftest":
            require(
                runtime
                == dict(device="CPU", inference_executed=False, responses="scripted wait actions"),
                "synthetic runtime mismatch",
            )
        else:
            require(
                runtime["sglang_version"] == "0.5.18"
                and "B200" in runtime["gpu"]
                and runtime["revision"] == guard.REVISION,
                "runtime mismatch",
            )
        events = sorted(n for n in names if n.startswith("events/"))
        require(events == [f"events/{i:06d}.json" for i in range(status["events"])], "event gap")
        expected = {"episodes/" + j["id"] + ".json" for j in planned["jobs"]}
        require({n for n in names if n.startswith("episodes/")} == expected, "missing episode")
        allowed = (
            {"MANIFEST.json", "plan.json", "runtime.json", "status.json"} | expected | set(events)
        )
        if planned.get("kind") == "transport_selftest":
            allowed.add("transport-fixture-padding.bin")
            padding = z.read("transport-fixture-padding.bin")
            require(
                len(padding) == planned["transport_fixture_bytes"]
                and sha(padding) == planned["transport_fixture_sha256"],
                "fixture padding mismatch",
            )
        require(set(names) == allowed, "unexpected artifact member")
        episodes = []
        for job in planned["jobs"]:
            ep = json.loads(z.read("episodes/" + job["id"] + ".json"))
            original.verify_episode(ep, planned, job)
            episodes.append(ep)
        return dict(
            verified_recovery_episodes=[ep["id"] for ep in episodes],
            quality={ep["id"]: ep["quality"] for ep in episodes},
            output_tokens={ep["id"]: ep["output_tokens"] for ep in episodes},
            input_tokens={ep["id"]: ep["input_tokens"] for ep in episodes},
            rank_eligible=False,
            b80_eligible=False,
        )


def worker(planned):
    import importlib.metadata
    import subprocess
    import urllib.request

    import torch

    root = Path(REMOTE)
    journal = Journal(root, planned)
    started = time.monotonic()
    server, error = None, None
    try:
        require(original.protocol_hash() == planned["protocol_hash"], "protocol mismatch")
        require(importlib.metadata.version("sglang") == "0.5.18", "sglang mismatch")
        require(
            torch.cuda.device_count() == 1 and "B200" in torch.cuda.get_device_name(0),
            "gpu mismatch",
        )
        journal.emit(
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
            require(server.poll() is None and time.monotonic() - started < 900, "readiness failure")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            time.sleep(2)

        def evaluate(payload):
            require(time.monotonic() - started < 1300, "inference time envelope")
            request = urllib.request.Request(
                "http://127.0.0.1:8000/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=240) as response:
                data = response.read(1048577)
                require(len(data) <= 1048576, "response size")
                return json.loads(data)

        for job in planned["jobs"]:
            ep = original.run_one(planned, job, evaluate, journal.emit)
            require(ep["status"] == "complete", "incomplete episode")
    except Exception as exc:
        error = type(exc).__name__
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    ready = journal.finish(error)
    print(
        json.dumps(dict(event="artifact_ready", archive_sha256=ready["archive_sha256"])), flush=True
    )
    ack_deadline = min(started + 1750, time.monotonic() + 120)
    while time.monotonic() < ack_deadline:
        ack = root / "ACK.json"
        if ack.exists():
            try:
                a = json.loads(ack.read_text())
                if a == dict(archive_sha256=ready["archive_sha256"], verified=True):
                    return 0 if error is None else 1
            except (OSError, ValueError):
                pass
        time.sleep(1)
    return 1


def collect(sandbox, planned, out, missing_error, deadline):
    while True:
        try:
            ready_raw = sandbox.filesystem.read_bytes(REMOTE + "/READY.json")
            ready = json.loads(ready_raw)
            break
        except missing_error:
            require(time.monotonic() < deadline, "artifact readiness timeout")
            require(sandbox.poll() is None, "sandbox ended before artifacts ready")
            time.sleep(2)
    require(0 < ready["archive_bytes"] <= 128 * 1024 * 1024, "artifact size limit")
    out.mkdir(parents=True, exist_ok=True)
    original.save(out / "READY.json", ready)
    sandbox.filesystem.copy_to_local(REMOTE + "/artifacts.zip", out / "artifacts.zip")
    data = (out / "artifacts.zip").read_bytes()
    verified = verify_archive(data, planned, ready)
    sandbox.filesystem.write_bytes(
        json.dumps(dict(archive_sha256=ready["archive_sha256"], verified=True)).encode(),
        REMOTE + "/ACK.json",
    )
    return verified


def launch(planned, receipt_path, out):
    require(planned == plan(), "plan changed")
    args = command(planned)
    source = source_bundle()
    require(len(source) <= 2097152, "source size")
    require(sha(source) == planned["source_archive_sha256"], "source changed before dispatch")
    receipt = json.loads(receipt_path.read_text())
    reserve = guard.check_receipt(receipt, planned)
    claim = Path(receipt["ledger_path"]).resolve().parent / (
        ".phonellm-probe-" + guard.sha(receipt["reservation_id"])[:24] + ".claimed"
    )
    with claim.open("x") as f:
        f.write(json.dumps(dict(plan_sha256=guard.sha(planned), claimed_at_unix=time.time())))
        f.flush()
        os.fsync(f.fileno())
    import modal
    from modal.exception import SandboxFilesystemNotFoundError

    require(modal.__version__ == "1.5.2", "SDK version changed")

    workspace = modal.Workspace.from_context()
    workspace.hydrate()
    require(workspace.name == receipt["workspace"], "workspace mismatch")
    out.mkdir(parents=True, exist_ok=False)
    (out / "source.zip").write_bytes(source)
    original.save(out / "plan.json", planned)
    result = dict(
        kind=planned["kind"],
        plan_sha256=guard.sha(planned),
        receipt_sha256=sha(receipt_path.read_bytes()),
        reservation_id=receipt["reservation_id"],
        app_name=APP,
        scheduled_kitchens=3,
        recovery_jobs=2,
        rank_eligible=False,
        b80_eligible=False,
        reserved_usd=reserve,
        known_total_cost_usd=None,
        cost_per_scheduled_kitchen_usd=None,
        cost_status="unreconciled; retain reserve",
        sdk=modal.__version__,
        local_before_create_unix=time.time(),
        verified=False,
    )
    sandbox = None
    original.save(out / "result.json", result)
    try:
        app = modal.App.lookup(APP, create_if_missing=True)
        sandbox = modal.Sandbox.create(
            *args,
            app=app,
            name="durable-" + guard.sha(receipt["reservation_id"])[:20],
            tags={"purpose": "phonellm-unranked-recovery", "experiment": guard.sha(planned)[:20]},
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
        original.save(out / "result.json", result)
        sandbox.stdin.write(source)
        sandbox.stdin.write_eof()
        sandbox.stdin.drain()
        result.update(
            collect(sandbox, planned, out, SandboxFilesystemNotFoundError, time.monotonic() + 1600)
        )
        sandbox.wait()
        result["exit_code"] = sandbox.returncode
        require(sandbox.returncode == 0, "worker exit")
        result["verified"] = True
        ids = set(planned["prior_known_usage"]) | set(result["output_tokens"])
        result["all_attempt_known_usage"] = {}
        for eid in sorted(ids):
            prior = planned["prior_known_usage"].get(eid, {})
            result["all_attempt_known_usage"][eid] = dict(
                input_tokens=prior.get("known_input_tokens", 0)
                + result["input_tokens"].get(eid, 0),
                output_tokens=prior.get("known_output_tokens", 0)
                + result["output_tokens"].get(eid, 0),
                unknown_unobserved_prior_usage=prior.get("unknown_unobserved_usage", False),
            )
        completed = planned["preserved_completed_job"]
        result["quality"][completed["id"]] = completed["quality"]
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
        original.save(out / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    planned = plan()
    if not args.launch:
        print(json.dumps(dict(plan_sha256=guard.sha(planned), plan=planned), indent=2))
        return 0
    if args.receipt is None or args.out is None:
        parser.error("launch requires new receipt and output directory")
    result = launch(planned, args.receipt, args.out)
    print(json.dumps(dict(artifact=str(args.out / "result.json"), verified=result["verified"])))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

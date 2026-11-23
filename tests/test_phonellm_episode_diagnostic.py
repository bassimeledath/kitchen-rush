"""Full-episode diagnostic checks with fake inference/Modal; no paid calls."""

import copy
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "phone_diagnostic", ROOT / "scripts/phonellm_episode_diagnostic.py"
)
phone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phone)
NOW = 1789680000.0


def answer(payload):
    return dict(
        model=phone.guard.MODEL,
        usage=dict(prompt_tokens=100, completion_tokens=16, total_tokens=116),
        choices=[dict(finish_reason="stop", message=dict(content='{"action":"wait(20)"}'))],
    )


def test_full_three_episode_replay_and_tampering():
    planned = phone.plan()
    assert [j["size"] for j in planned["jobs"]] == [2, 3, 4]
    assert [j["seed"] for j in planned["jobs"]] == [74002, 74003, 74004]
    for job in planned["jobs"]:
        ep = phone.run_one(planned, job, answer, lambda *a, **kw: None)
        assert phone.verify_episode(ep, planned, job)
        assert ep["cost_usd"] is None and not ep["rank_eligible"] and not ep["b80_eligible"]
        bad = copy.deepcopy(ep)
        bad["requests"][0]["canonical"]["state"]["secret"] = "hidden"
        with pytest.raises(ValueError):
            phone.verify_episode(bad, planned, job)
        bad = copy.deepcopy(ep)
        bad["output_tokens"] += 1
        with pytest.raises(ValueError):
            phone.verify_episode(bad, planned, job)


def test_token_boundary_invalid_selection_and_transport_failure():
    p = phone.plan()
    job = p["jobs"][0]
    calls = []

    def exhaust(payload):
        calls.append(payload["max_tokens"])
        n = payload["max_tokens"]
        return dict(
            model=phone.guard.MODEL,
            usage=dict(prompt_tokens=100, completion_tokens=n, total_tokens=100 + n),
            choices=[dict(finish_reason="length", message=dict(content=""))],
        )

    ep = phone.run_one(p, job, exhaust, lambda *a, **kw: None)
    assert calls == [8192] and ep["output_tokens"] == 8192 and ep["invalid"] == 1
    assert ep["reason"] == "token_budget_exhausted" and phone.verify_episode(ep, p, job)
    events = []

    def fail(payload):
        raise TimeoutError()

    ep = phone.run_one(p, job, fail, lambda event, **values: events.append(event))
    assert ep["status"] == "quarantined" and not ep["actions"]
    assert ep["requests"][0]["admitted"] is False and "response" not in ep["requests"][0]
    assert ep["cost_usd"] is None and events[-1] == "episode_finished"
    with pytest.raises(ValueError):
        phone.verify_episode(ep, p, job)


def test_fake_sandbox_full_lifecycle_receipt_and_single_use(tmp_path):
    p = phone.plan()
    events = [
        json.dumps(
            dict(
                event="runtime_verified",
                sglang_version="0.5.18",
                gpu="NVIDIA B200",
                revision=phone.guard.REVISION,
            )
        )
    ]
    for job in p["jobs"]:
        phone.run_one(
            p,
            job,
            answer,
            lambda event, **values: events.append(json.dumps(dict(event=event, **values))),
        )
    db = tmp_path / "spend.sqlite"
    with sqlite3.connect(db) as con:
        con.executescript(
            "CREATE TABLE meta(id INTEGER,cap INTEGER); "
            "INSERT INTO meta VALUES(1,200000000000); "
            "CREATE TABLE opening(id INTEGER,amount INTEGER); INSERT INTO opening VALUES(1,0); "
            "CREATE TABLE requests(id TEXT,reserved INTEGER,actual INTEGER,status TEXT); "
            "INSERT INTO requests VALUES('full-episodes',5000000000,NULL,'reserved');"
        )
    evidence = tmp_path / "price.txt"
    evidence.write_text("offline price fixture")
    receipt = dict(
        plan_sha256=phone.guard.sha(p),
        reserved_usd=5,
        ledger_path=str(db),
        reservation_id="full-episodes",
        workspace="offline",
        existing_image_id=phone.guard.EXISTING_IMAGE_ID,
        resource_envelope_verified=True,
        verified_at_unix=NOW,
        price_evidence_path=str(evidence),
        price_evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
    )
    rp = tmp_path / "receipt.json"
    rp.write_text(json.dumps(receipt))
    calls = []
    sandbox = SimpleNamespace(
        object_id="sb-fake",
        stdin=SimpleNamespace(
            write=lambda value: calls.append(("stdin_write", value)),
            write_eof=lambda: calls.append(("stdin_eof", None)),
            drain=lambda: calls.append(("stdin_drain", None)),
        ),
        stdout=events,
        returncode=0,
        wait=lambda: None,
        terminate=lambda **kw: calls.append(("terminate", kw)),
    )
    fake = SimpleNamespace(
        __version__="fake",
        Workspace=SimpleNamespace(
            from_context=lambda: SimpleNamespace(name="offline", hydrate=lambda: None)
        ),
        App=SimpleNamespace(lookup=lambda *a, **kw: "app"),
        Image=SimpleNamespace(from_id=lambda image_id: image_id),
        Sandbox=SimpleNamespace(create=lambda *a, **kw: calls.append(("create", kw)) or sandbox),
    )
    with (
        patch.dict(sys.modules, {"modal": fake}),
        patch.object(phone.time, "time", return_value=NOW),
    ):
        result = phone.launch(p, rp, tmp_path / "out")
        with pytest.raises(FileExistsError):
            phone.launch(p, rp, tmp_path / "again")
    assert [c[0] for c in calls] == [
        "create", "stdin_write", "stdin_eof", "stdin_drain", "terminate"
    ]
    assert calls[1][1] == phone.bundle() and result["source_transfer_complete"]
    assert phone.verify_saved(tmp_path / "out")["episodes"] == 3
    assert result["all_three_complete_and_verified"] and result["known_total_cost_usd"] is None
    assert result["cost_per_scheduled_kitchen_usd"] is None and result["scheduled_kitchens"] == 3
    assert not result["rank_eligible"] and not result["b80_eligible"]
    cfg = calls[0][1]
    assert cfg["cpu"] == (4, 4) and cfg["memory"] == (65536, 65536) and cfg["timeout"] == 1800
    assert cfg["secrets"] == [] and cfg["volumes"] == {} and "region" not in cfg
    assert (
        hashlib.sha256((tmp_path / "out/source.zip").read_bytes()).hexdigest()
        == p["source_archive_sha256"]
    )
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT actual,status FROM requests").fetchone() == (None, "reserved")


def test_source_bundle_reproduces_plan_without_checkout(tmp_path):
    with zipfile.ZipFile(io.BytesIO(phone.bundle())) as archive:
        archive.extractall(tmp_path)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts/phonellm_episode_diagnostic.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["plan"] == phone.plan()


def test_stdin_bootstrap_integrity_and_actual_modal_cmd_limit():
    """Exercise actual SDK validation offline; no client or authentication is loaded."""
    sdk_python = Path.home() / ".local/share/uv/tools/modal/bin/python"
    if not sdk_python.exists():
        pytest.skip("Installed Modal SDK runtime is unavailable")
    args = phone.launch_args(phone.plan())
    validate = """import json,sys
from modal.sandbox import _validate_exec_args,ARG_MAX_BYTES
from modal.exception import InvalidError
a=json.load(sys.stdin)
_validate_exec_args(a)
assert ARG_MAX_BYTES==65536
try:
 _validate_exec_args(['x'*32769,'x'*32768])
except InvalidError:
 pass
else:
 raise AssertionError('total argument limit was not enforced')
print(sum(map(len,a)))
"""
    checked = subprocess.run(
        [str(sdk_python), "-c", validate], input=json.dumps(args), text=True,
        capture_output=True, check=True,
    )
    assert int(checked.stdout) < 65536
    with pytest.raises(ValueError, match="sandbox_cmd_too_large"):
        phone.launch_args({"oversized": "x" * 65536})
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(
            "scripts/phonellm_episode_diagnostic.py",
            "def worker(p):\n print(p['marker'])\n",
        )
    source = archive.getvalue()
    p = dict(source_archive_sha256=hashlib.sha256(source).hexdigest(), marker="verified")
    cmd = [sys.executable, "-c", phone.BOOTSTRAP, json.dumps(p)]
    run = subprocess.run(cmd, input=source, capture_output=True, check=True)
    assert run.stdout == b"verified\n"
    bad = subprocess.run(cmd, input=source + b"tampered", capture_output=True)
    assert bad.returncode != 0 and b"source_transfer_integrity" in bad.stderr

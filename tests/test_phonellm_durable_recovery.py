"""Offline durable transfer, framing failure, SDK signature and replay checks."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "durable_fixture", ROOT / "scripts/phonellm_durable_recovery.py"
)
durable = importlib.util.module_from_spec(spec)
spec.loader.exec_module(durable)


@pytest.fixture(autouse=True)
def portable_phone_module(phone_checkout, monkeypatch):
    fixture_spec = importlib.util.spec_from_file_location(
        "portable_durable_fixture", phone_checkout / "scripts/phonellm_durable_recovery.py"
    )
    fixture_module = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture_module)
    monkeypatch.setitem(globals(), "ROOT", phone_checkout)
    monkeypatch.setitem(globals(), "durable", fixture_module)


def make_archive(tmp_path):
    planned = durable.plan()
    journal = durable.Journal(tmp_path, planned)
    journal.emit(
        "runtime_verified",
        sglang_version="0.5.18",
        gpu="NVIDIA B200",
        revision=durable.guard.REVISION,
    )
    for job in planned["jobs"]:

        def evaluate(payload):
            return dict(
                model=durable.guard.MODEL,
                usage=dict(prompt_tokens=100, completion_tokens=16, total_tokens=116),
                choices=[dict(finish_reason="stop", message=dict(content='{"action":"wait(20)"}'))],
            )

        durable.original.run_one(planned, job, evaluate, journal.emit)
    ready = journal.finish()
    return planned, ready, (tmp_path / "artifacts.zip").read_bytes()


def test_scope_preserves_completed_and_prior_usage():
    planned = durable.plan()
    assert [j["size"] for j in planned["jobs"]] == [3, 4]
    assert [j["seed"] for j in planned["jobs"]] == [74003, 74004]
    assert planned["preserved_completed_job"]["quality"] == 0
    assert planned["scheduled_kitchens"] == 3 and planned["recovery_jobs"] == 2
    assert planned["token_cap"] == 8192 and planned["max_decisions"] == 160
    assert not planned["rank_eligible"] and not planned["b80_eligible"]
    old = planned["prior_known_usage"]["phonellm-development-3-seed74003-t8192"]
    assert old["known_output_tokens"] > 0 and old["unknown_unobserved_usage"]


def test_verified_transfer_acks_only_after_checksums_and_replay(tmp_path):
    p, ready, data = make_archive(tmp_path / "worker")
    writes = []

    def copy(remote, local):
        Path(local).write_bytes(data)

    sandbox = SimpleNamespace(
        filesystem=SimpleNamespace(
            read_bytes=lambda path: json.dumps(ready).encode(),
            copy_to_local=copy,
            write_bytes=lambda value, path: writes.append((value, path)),
        )
    )
    verified = durable.collect(sandbox, p, tmp_path / "host", FileNotFoundError, 0)
    assert len(verified["verified_recovery_episodes"]) == 2
    assert len(writes) == 1 and json.loads(writes[0][0])["verified"] is True
    assert writes[0][1].endswith("/ACK.json")


@pytest.mark.parametrize("damage", ["partial", "corrupt", "missing"])
def test_failed_transfer_never_acks(tmp_path, damage):
    p, ready, data = make_archive(tmp_path / "worker")
    writes = []

    def copy(remote, local):
        if damage == "missing":
            raise FileNotFoundError()
        Path(local).write_bytes(data[:-1] if damage == "partial" else b"x" + data[1:])

    sandbox = SimpleNamespace(
        filesystem=SimpleNamespace(
            read_bytes=lambda path: json.dumps(ready).encode(),
            copy_to_local=copy,
            write_bytes=lambda *args: writes.append(args),
        )
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        durable.collect(sandbox, p, tmp_path / "host", FileNotFoundError, 0)
    assert not writes


def test_missing_ready_is_failure_not_success(tmp_path):
    def missing(path):
        raise FileNotFoundError()

    sandbox = SimpleNamespace(filesystem=SimpleNamespace(read_bytes=missing), poll=lambda: 0)
    with pytest.raises(ValueError):
        durable.collect(sandbox, durable.plan(), tmp_path, FileNotFoundError, 0)


def test_cli_failure_returns_nonzero():
    with (
        patch.object(sys, "argv", ["runner", "--launch", "--receipt", "unused", "--out", "unused"]),
        patch.object(durable, "launch", return_value={"verified": False}),
    ):
        assert durable.main() == 1


def test_actual_sdk_signatures_python310_and_cmd(modal_sdk_python):
    python = modal_sdk_python
    code = """import inspect,json,sys
from modal.sandbox import _validate_exec_args
from modal.sandbox_fs import _SandboxFilesystem
assert sys.version_info[:2]==(3,10)
_validate_exec_args(json.load(sys.stdin))
methods=[('read_bytes',['self','remote_path']),
         ('copy_to_local',['self','remote_path','local_path']),
         ('write_bytes',['self','data','remote_path'])]
for name,parameters in methods:
 assert list(inspect.signature(getattr(_SandboxFilesystem,name)).parameters)==parameters
print('signatures verified')
"""
    subprocess.run(
        [str(python), "-c", code],
        input=json.dumps(durable.command(durable.plan())),
        text=True,
        capture_output=True,
        check=True,
    )
    completed = subprocess.run(
        [str(python), str(ROOT / "scripts/phonellm_durable_recovery.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout)["plan"] == durable.plan()

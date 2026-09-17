"""CPU transport preflight is offline unless explicitly launched with a fresh receipt."""

import copy
import importlib.util
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
    "cpu_transport_fixture", ROOT / "scripts/phonellm_cpu_transport_check.py"
)
cpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu)
NOW = 1789680000.0


def receipt_fixture(tmp_path, planned):
    ledger = tmp_path / "ledger.sqlite"
    with sqlite3.connect(ledger) as db:
        db.executescript(
            "CREATE TABLE meta(id,cap); INSERT INTO meta VALUES(1,200000000000);"
            "CREATE TABLE opening(id,amount); INSERT INTO opening VALUES(1,0);"
            "CREATE TABLE requests(id,reserved,actual,status);"
            "INSERT INTO requests VALUES("
            "'phonellm-cpu-transport-fixture',250000000,NULL,'reserved');"
        )
    evidence = tmp_path / "price.json"
    evidence.write_text("{}")
    receipt = dict(
        plan_sha256=cpu.d.guard.sha(planned),
        reserved_usd=0.25,
        reservation_id="phonellm-cpu-transport-fixture",
        verified_at_unix=NOW,
        resource_envelope_verified=True,
        existing_image_id=cpu.d.guard.EXISTING_IMAGE_ID,
        price_evidence_path=str(evidence),
        price_evidence_sha256=cpu.d.sha(evidence.read_bytes()),
        ledger_path=str(ledger),
        workspace="offline",
    )
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    return path, receipt


def test_cpu_guard_bounds_distinct_claim_and_expiry(tmp_path):
    p = cpu.plan()
    _, r = receipt_fixture(tmp_path, p)
    assert cpu.check_receipt(r, p, NOW) == 0.25
    for key, value in [
        ("reserved_usd", 5),
        ("reserved_usd", 0),
        ("reservation_id", "phonellm-full-episodes-1"),
        ("existing_image_id", "different"),
    ]:
        bad = copy.deepcopy(r)
        bad[key] = value
        with pytest.raises(ValueError):
            cpu.check_receipt(bad, p, NOW)
    with pytest.raises(ValueError):
        cpu.check_receipt(r, p, NOW + 901)
    assert p["kind"] == "transport_selftest" and p["gpu"] is None and not p["inference_executed"]


def test_scripted_journal_replays_without_fake_gpu_claims(tmp_path):
    p = cpu.plan()
    ready = cpu.make_artifacts(p, tmp_path)
    result = cpu.d.verify_archive((tmp_path / "artifacts.zip").read_bytes(), p, ready)
    assert ready["archive_bytes"] > 3 * 1024 * 1024
    assert len(result["verified_recovery_episodes"]) == 2
    with zipfile.ZipFile(tmp_path / "artifacts.zip") as z:
        runtime = json.loads(z.read("runtime.json"))
        assert runtime == dict(
            device="CPU", inference_executed=False, responses="scripted wait actions"
        )
        assert max(info.file_size for info in z.infolist()) > 8192
        assert len(z.read("transport-fixture-padding.bin")) == 3 * 1024 * 1024
    production = dict(p, kind="unranked_phonellm_durable_recovery_v1")
    # Synthetic metadata must never satisfy the production verifier.
    with pytest.raises(ValueError):
        cpu.d.verify_archive((tmp_path / "artifacts.zip").read_bytes(), production, ready)


def test_fake_full_sdk_lifecycle_cpu_only_and_no_ledger_write(tmp_path):
    p = cpu.plan()
    rp, r = receipt_fixture(tmp_path, p)
    remote = tmp_path / "remote"
    cpu.make_artifacts(p, remote)
    calls = []

    def copy_file(remote_path, local_path):
        Path(local_path).write_bytes((remote / Path(remote_path).name).read_bytes())

    sandbox = SimpleNamespace(
        object_id="sb-fake",
        returncode=0,
        wait=lambda: None,
        poll=lambda: None,
        stdin=SimpleNamespace(
            write=lambda b: calls.append(("stdin", b)), write_eof=lambda: None, drain=lambda: None
        ),
        filesystem=SimpleNamespace(
            read_bytes=lambda p: (remote / Path(p).name).read_bytes(),
            copy_to_local=copy_file,
            write_bytes=lambda b, p: calls.append(("ack", b)),
        ),
        terminate=lambda **kw: calls.append(("terminate", kw)),
    )

    def create(*a, **kw):
        calls.append(("create", kw))
        return sandbox

    fake = SimpleNamespace(
        __version__="1.5.2",
        Workspace=SimpleNamespace(
            from_context=lambda: SimpleNamespace(name="offline", hydrate=lambda: None)
        ),
        App=SimpleNamespace(lookup=lambda *a, **kw: "app"),
        Image=SimpleNamespace(from_id=lambda x: x),
        Sandbox=SimpleNamespace(create=create),
    )
    with (
        patch.dict(
            sys.modules,
            {
                "modal": fake,
                "modal.exception": SimpleNamespace(
                    SandboxFilesystemNotFoundError=FileNotFoundError
                ),
            },
        ),
        patch.object(cpu.time, "time", return_value=NOW),
    ):
        result = cpu.launch(p, rp, tmp_path / "out")
        with pytest.raises(FileExistsError):
            cpu.launch(p, rp, tmp_path / "again")
    assert result["verified"] and result["known_total_cost_usd"] is None
    cfg = next(value for name, value in calls if name == "create")
    assert (
        cfg["gpu"] is None
        and cfg["cpu"] == (1, 1)
        and cfg["memory"] == (1024, 1024)
        and cfg["timeout"] == 120
    )
    assert cfg["volumes"] == {} and cfg["secrets"] == []
    assert [name for name, _ in calls] == ["create", "stdin", "ack", "terminate"]
    with sqlite3.connect(r["ledger_path"]) as db:
        assert db.execute("SELECT actual,status FROM requests").fetchone() == (None, "reserved")


def test_python310_actual_sdk_validator():
    python = Path.home() / ".local/share/uv/tools/modal/bin/python"
    if not python.is_file():
        pytest.skip("Modal runtime unavailable")
    subprocess.run(
        [
            str(python),
            "-c",
            "import json,sys; from modal.sandbox import _validate_exec_args; "
            "_validate_exec_args(json.load(sys.stdin))",
        ],
        input=json.dumps(cpu.command(cpu.plan())),
        text=True,
        capture_output=True,
        check=True,
    )
    result = subprocess.run(
        [str(python), str(ROOT / "scripts/phonellm_cpu_transport_check.py")],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["plan"] == cpu.plan()

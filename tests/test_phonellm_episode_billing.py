"""Offline checks for scoped diagnostic billing evidence; no provider requests."""

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "episode_billing", ROOT / "scripts/reconcile_phonellm_episodes.py"
)
billing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(billing)
START = 1789632000
END = START + 3600


def fixture(monkeypatch, tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            dict(reservation_id=billing.RESERVATION, plan_sha256="plan", workspace="fixture")
        )
    )
    result = dict(
        kind="unranked_phonellm_full_episode_diagnostic",
        reservation_id=billing.RESERVATION,
        receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        plan_sha256="plan",
        rank_eligible=False,
        scheduled_kitchens=3,
        sandbox_id="sb-fixture",
        termination_confirmed=True,
        exit_code=0,
        local_before_create_unix=START + 200,
        local_after_terminate_unix=START + 1000,
    )
    path = tmp_path / "result.json"
    path.write_text(json.dumps(result))
    monkeypatch.setattr(billing, "RESULT", path)
    monkeypatch.setattr(billing, "RECEIPT", receipt)
    monkeypatch.setattr(billing, "OUTPUT", tmp_path / "billing.json")
    return result


def test_terminal_result_and_receipt_required(monkeypatch, tmp_path):
    result = fixture(monkeypatch, tmp_path)
    assert billing.evidence()[2:5] == (START, END, END + 180)
    for field, value in (
        ("termination_confirmed", False),
        ("receipt_sha256", "tampered"),
        ("reservation_id", "first-probe"),
        ("exit_code", None),
    ):
        bad = {**result, field: value}
        billing.RESULT.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            billing.evidence()


def test_early_fetch_and_duplicate_blocked(monkeypatch, tmp_path):
    fixture(monkeypatch, tmp_path)
    monkeypatch.setattr("sys.argv", ["billing", "--fetch"])
    fetch = AsyncMock(return_value={"status": "fixture_only"})
    monkeypatch.setattr(billing, "fetch", fetch)
    monkeypatch.setattr(billing.time, "time", lambda: END + 179)
    with pytest.raises(SystemExit, match="Refusing early"):
        billing.main()
    fetch.assert_not_called()
    assert not billing.OUTPUT.exists()
    monkeypatch.setattr(billing.time, "time", lambda: END + 180)
    billing.main()
    fetch.assert_awaited_once()
    with pytest.raises(FileExistsError):
        billing.main()
    fetch.assert_awaited_once()


def test_empty_fetch_is_not_zero_cost(monkeypatch, tmp_path):
    fixture(monkeypatch, tmp_path)
    billing.evidence()
    summary = billing.shared.summarize_rows([], "ap-fixture", START, END)
    assert summary["gross_reported_cost_usd"] is None
    assert summary["status"] == "needs_review"

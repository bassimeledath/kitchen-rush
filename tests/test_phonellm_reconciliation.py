"""Offline billing-evidence safeguards; these tests never contact Modal."""

import importlib.util
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "reconcile", Path(__file__).resolve().parents[1] / "scripts/reconcile_phonellm_probe.py"
)
reconcile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile)
UTC = timezone.utc  # noqa: UP017 -- mirror Python 3.10-compatible script
START = 1789632000
END = START + 3600


def lifecycle():
    return {
        "sandbox_id": reconcile.SANDBOX,
        "termination_confirmed": True,
        "exit_code": 0,
        "local_before_create_unix": START + 176,
        "local_after_terminate_unix": START + 607,
    }


def row(object_id, cost="1.25"):
    return SimpleNamespace(
        object_id=object_id,
        interval_start=datetime.fromtimestamp(START, UTC),
        cost=Decimal(cost),
        cost_by_resource={"gpu_b200": Decimal(cost)},
        tags={"probe": "fixture", "unrelated": "must not serialize"},
    )


def test_gross_rows_are_scoped_decimal_and_not_double_counted():
    summary = reconcile.summarize_rows([row(reconcile.SANDBOX)], "ap-probe", START, END)
    assert summary["gross_reported_cost_usd"] == "1.25"
    assert summary["gross_by_resource_usd"] == {"gpu_b200": "1.25"}
    assert summary["attribution"] == "sandbox"
    assert summary["rows"][0]["tags"] == {"probe": "fixture"}
    for items in ([], [row("ap-unrelated")], [row("ap-probe"), row(reconcile.SANDBOX)]):
        report = reconcile.summarize_rows(items, "ap-probe", START, END)
        assert report["gross_reported_cost_usd"] is None
        assert report["problems"]
        assert "ap-unrelated" not in json.dumps(report)


def test_missing_zero_or_bad_breakdown_remain_unreconciled():
    broken = row(reconcile.SANDBOX)
    broken.cost_by_resource = {}
    for items in ([row(reconcile.SANDBOX, "0")], [broken], [broken, broken]):
        report = reconcile.summarize_rows(items, "ap-probe", START, END)
        assert report["gross_reported_cost_usd"] is None
        assert report["status"] == "needs_review"


def test_closed_hour_buffer_and_permanent_once_only_claim(monkeypatch, tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps(lifecycle()))
    output = tmp_path / "billing.json"
    monkeypatch.setattr(reconcile, "RESULT", result)
    monkeypatch.setattr(reconcile, "OUTPUT", output)
    monkeypatch.setattr("sys.argv", ["reconcile", "--fetch"])
    fetch = AsyncMock(return_value={"status": "fixture_only"})
    monkeypatch.setattr(reconcile, "fetch", fetch)
    assert reconcile.query_window(lifecycle()) == (START, END, END + 180)
    monkeypatch.setattr(reconcile.time, "time", lambda: END + 179)
    with pytest.raises(SystemExit, match="Refusing early"):
        reconcile.main()
    fetch.assert_not_called()
    assert not output.exists()
    monkeypatch.setattr(reconcile.time, "time", lambda: END + 180)
    reconcile.main()
    fetch.assert_awaited_once_with(START, END)
    with pytest.raises(FileExistsError):
        reconcile.main()
    fetch.assert_awaited_once()

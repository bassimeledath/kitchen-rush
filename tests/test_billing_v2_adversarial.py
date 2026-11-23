"""Adversarial billing responses through fake native clients; no live requests."""

import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "billing_adversarial", ROOT / "scripts/reconcile_phonellm_billing_v2.py"
)
billing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(billing)
UTC = timezone.utc  # noqa: UP017 -- target runtime is Python 3.10


def row(object_id="sb-target", cost="0.12", resources=None):
    return SimpleNamespace(
        object_id=object_id,
        interval_start=datetime.fromtimestamp(0, UTC),
        cost=cost,
        cost_by_resource={"cpu": cost} if resources is None else resources,
        tags={"purpose": "fixture", "private_tag": "must-not-appear"},
        description="private description must not appear",
    )


def fake_fetch(rows):
    async def run():
        events = []

        async def hydrate():
            events.append("hydrate")

        async def close():
            events.append("close")

        async def stream(request):
            assert request.app_ids == ["ap-target"]
            assert request.resolution == "h"
            events.append("scoped_stream")
            for item in rows:
                yield item

        client = SimpleNamespace(
            stub=SimpleNamespace(WorkspaceBillingReport=SimpleNamespace(unary_stream=stream)),
            _close=close,
        )
        workspace = SimpleNamespace(name="expected", client=client, hydrate=hydrate)

        async def lookup(name, **kwargs):
            assert name == "dedicated-app"
            assert kwargs == {"client": client, "create_if_missing": False}
            events.append("lookup_without_creation")
            return SimpleNamespace(app_id="ap-target")

        timestamp = SimpleNamespace(FromDatetime=lambda value: None)

        def request_factory(**kwargs):
            return SimpleNamespace(**kwargs, start_timestamp=timestamp, end_timestamp=timestamp)

        result = await billing.bounded_fetch(
            dict(
                workspace="expected", app="dedicated-app", sandbox_id="sb-target", start=0, end=3600
            ),
            timeout=1,
            dependencies=(lambda: workspace, lookup, request_factory, lambda value: value),
        )
        assert events == ["hydrate", "lookup_without_creation", "scoped_stream", "close"]
        assert "must-not-appear" not in json.dumps(result)
        assert "private description" not in json.dumps(result)
        return result

    return asyncio.run(run())


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row(cost="0")],
        [row(cost="-1")],
        [row(cost="NaN")],
        [row(cost="Infinity")],
        [row(cost="not-a-number")],
        [row(cost=None)],
        [row(resources={})],
        [row(resources={"cpu": "0.01"})],
        [row(resources={"cpu": "NaN"})],
        [row("ap-unrelated")],
        [row("ap-target"), row("sb-target")],
        [row(), row()],
    ],
)
def test_ambiguous_or_malformed_usage_never_becomes_reported_total(rows):
    result = fake_fetch(rows)
    assert result["status"] in ("needs_review", "fetch_failed_reserve_unchanged")
    assert result.get("gross_reported_cost_usd") is None
    assert not result.get("gross_by_resource_usd")
    assert "ap-unrelated" not in json.dumps(result)


def test_app_total_explicitly_requires_isolation_and_sandbox_total_does_not():
    app = fake_fetch([row("ap-target")])
    assert app["gross_reported_cost_usd"] == "0.12"
    assert app["attribution"] == "app" and app["app_isolation_proof_required"] is True
    sandbox = fake_fetch([row()])
    assert sandbox["gross_reported_cost_usd"] == "0.12"
    assert sandbox["attribution"] == "sandbox" and sandbox["app_isolation_proof_required"] is False

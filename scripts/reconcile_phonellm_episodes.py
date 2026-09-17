#!/usr/bin/env python3
"""Once-only gross billing evidence for the second full-episode diagnostic launch.

Offline by default; --fetch requires terminal evidence and a closed hour plus 3 minutes.
Never dispatches compute, changes a ledger, or queries unrelated applications.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "runs/v2/phonellm-episodes-20260917"
RESULT = FOLDER / "execution-attempt2/result.json"
RECEIPT = FOLDER / "receipt-attempt2.json"
OUTPUT = FOLDER / "gross-billing-attempt2.json"
APP = "kitchenrush-phonellm-full-episode-diagnostic"
RESERVATION = "phonellm-full-episodes-20260917-2"
UTC = timezone.utc  # noqa: UP017 -- installed Modal uses Python 3.10
HELPER = ROOT / "scripts/reconcile_phonellm_probe.py"
_spec = importlib.util.spec_from_file_location("isolated_probe_billing", HELPER)
shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shared)


def evidence():
    raw = RESULT.read_bytes()
    receipt_raw = RECEIPT.read_bytes()
    result, receipt = json.loads(raw), json.loads(receipt_raw)
    sandbox = result.get("sandbox_id")
    if (
        result.get("kind") != "unranked_phonellm_full_episode_diagnostic"
        or result.get("reservation_id") != RESERVATION
        or receipt.get("reservation_id") != RESERVATION
        or result.get("receipt_sha256") != hashlib.sha256(receipt_raw).hexdigest()
        or result.get("plan_sha256") != receipt.get("plan_sha256")
        or result.get("rank_eligible") is not False
        or result.get("scheduled_kitchens") != 3
        or not isinstance(sandbox, str)
        or not sandbox.startswith("sb-")
    ):
        raise ValueError("diagnostic result/receipt binding invalid")
    # This is an isolated imported module; the first probe's script/process is unchanged.
    shared.SANDBOX = sandbox
    start, end, earliest = shared.query_window(result)
    return (
        result,
        receipt,
        start,
        end,
        earliest,
        {
            "result_sha256": hashlib.sha256(raw).hexdigest(),
            "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "plan_sha256": receipt["plan_sha256"],
        },
    )


async def fetch(receipt, start, end):
    import modal
    from modal._workspace import _Workspace
    from modal.types import BillingReportItem
    from modal_proto import api_pb2

    if modal.__version__ != "1.5.2":
        raise ValueError("SDK changed; review billing transport")
    workspace = _Workspace.from_context()
    await workspace.hydrate()
    if workspace.name != receipt["workspace"]:
        raise ValueError("unexpected workspace")
    app = await modal.App.lookup.aio(APP, client=workspace.client, create_if_missing=False)
    if not isinstance(app.app_id, str) or not app.app_id.startswith("ap-"):
        raise ValueError("missing dedicated app identity")
    request = api_pb2.WorkspaceBillingReportRequest(
        app_ids=[app.app_id], resolution="h", tag_names=["purpose", "experiment"]
    )
    request.start_timestamp.FromDatetime(datetime.fromtimestamp(start, UTC))
    request.end_timestamp.FromDatetime(datetime.fromtimestamp(end, UTC))
    rows = [
        BillingReportItem._from_proto(row)
        async for row in workspace.client.stub.WorkspaceBillingReport.unary_stream(request)
    ]
    # Shared allowlist deliberately omits free-form descriptions and account data.
    return {
        "app_id": app.app_id,
        "sdk": modal.__version__,
        **shared.summarize_rows(rows, app.app_id, start, end),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    result, receipt, start, end, earliest, hashes = evidence()
    record = dict(
        **hashes,
        sandbox_id=result["sandbox_id"],
        app_name=APP,
        reservation_id=RESERVATION,
        fetch_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        shared_helper_sha256=hashlib.sha256(HELPER.read_bytes()).hexdigest(),
        query_start_utc=datetime.fromtimestamp(start, UTC).isoformat(),
        query_end_exclusive_utc=datetime.fromtimestamp(end, UTC).isoformat(),
        earliest_fetch_utc=datetime.fromtimestamp(earliest, UTC).isoformat(),
        resolution="h",
        collection_buffer_seconds=180,
        scheduled_kitchens=3,
        cost_basis="gross provider-reported usage before credits and reservations",
        allocation="Entire gross session cost / 3 scheduled kitchens, including startup, "
        "failed work, idle and shutdown; not per-action dollar admission.",
        limitations="Collection delay has no documented maximum or finality flag. "
        "App-level rows require proof of no other compute in that app/window. "
        "The earlier locally rejected launch created no compute; retain its diagnosis. "
        "Absent/zero rows never establish zero cost. No ledger settlement.",
        billing_documentation="https://modal.com/docs/guide/billing",
        ledger_modified=False,
    )
    if not args.fetch:
        print(json.dumps(record, indent=2))
        return
    if time.time() < earliest:
        raise SystemExit("Refusing early billing fetch; earliest: " + record["earliest_fetch_utc"])
    with OUTPUT.open("x") as file:
        record.update(status="fetch_started", requested_at_utc=datetime.now(UTC).isoformat())
        file.write(json.dumps(record, indent=2) + "\n")
        file.flush()
        os.fsync(file.fileno())
        try:
            record.update(asyncio.run(fetch(receipt, start, end)))
        except Exception as exc:
            record.update(status="fetch_failed_reserve_unchanged", error_type=type(exc).__name__)
        record["finished_at_utc"] = datetime.now(UTC).isoformat()
        file.seek(0)
        file.write(json.dumps(record, indent=2) + "\n")
        file.truncate()
    print(json.dumps(dict(artifact=str(OUTPUT), status=record["status"], ledger_modified=False)))


if __name__ == "__main__":
    main()

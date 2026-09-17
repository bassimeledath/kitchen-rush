#!/usr/bin/env python3
"""One read-only, app-scoped gross billing fetch; never settles or edits a ledger.

Default: print the closed-hour query and earliest fetch time without contacting Modal.
--fetch: claim one output file permanently, query once, retain only allowlisted rows.
Compatible with the existing Modal SDK 1.5.2 / Python 3.10 interpreter.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "runs/v2/phonellm-probe-20260917"
RESULT = FOLDER / "execution/result.json"
OUTPUT = FOLDER / "gross-billing-fetch.json"
SANDBOX = "sb-t7CY44k1Ye28Tb4fMRYECh"
APP = "kitchenrush-phonellm-bounded-probe"
UTC = timezone.utc  # noqa: UP017 -- installed Modal interpreter is Python 3.10


def query_window(result):
    if result.get("sandbox_id") != SANDBOX or result.get("termination_confirmed") is not True:
        raise ValueError("matching confirmed sandbox termination required")
    first = result["local_before_create_unix"]
    last = result["local_after_terminate_unix"]
    if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (first, last)):
        raise ValueError("invalid lifecycle timestamps")
    if last < first or result.get("exit_code") is None:
        raise ValueError("incomplete lifecycle evidence")
    start = math.floor(first / 3600) * 3600
    end = (math.floor(last / 3600) + 1) * 3600
    return start, end, end + 180


def summarize_rows(rows, app_id, start, end):
    """Drop unrelated data before serialization; reject ambiguous or incomplete evidence."""
    safe, problems, seen = [], [], set()
    for row in rows:
        if row.object_id not in (app_id, SANDBOX):
            problems.append("unexpected_object_in_app_scoped_response")
            continue
        stamp = row.interval_start.timestamp()
        key = (row.object_id, stamp)
        if key in seen or not start <= stamp < end or stamp % 3600:
            problems.append("duplicate_or_out_of_range_interval")
            continue
        seen.add(key)
        cost = Decimal(row.cost)
        resources = {k: Decimal(v) for k, v in row.cost_by_resource.items()}
        if any(not c.is_finite() or c < 0 for c in [cost, *resources.values()]):
            problems.append("invalid_cost")
            continue
        if not resources or abs(sum(resources.values(), Decimal(0)) - cost) > Decimal("1e-8"):
            problems.append("resource_breakdown_missing_or_mismatched")
        safe.append(
            {
                "object_id": row.object_id,
                "interval_start_utc": row.interval_start.isoformat(),
                "gross_cost_usd": str(cost),
                "cost_by_resource_usd": {k: str(v) for k, v in resources.items()},
                "tags": {k: v for k, v in row.tags.items() if k in ("purpose", "probe")},
            }
        )
    objects = {row["object_id"] for row in safe}
    if len(objects) > 1:
        problems.append("mixed_app_and_sandbox_granularity_do_not_sum")
    total = sum((Decimal(row["gross_cost_usd"]) for row in safe), Decimal(0))
    if not safe or total == 0:
        problems.append("absent_or_zero_data_not_evidence_of_zero_usage")
    by_resource = {}
    for row in safe:
        for key, value in row["cost_by_resource_usd"].items():
            by_resource[key] = by_resource.get(key, Decimal(0)) + Decimal(value)
    return {
        "status": "needs_review" if problems else "gross_usage_reported_requires_review",
        "rows": safe,
        "problems": sorted(set(problems)),
        "gross_reported_cost_usd": str(total) if not problems else None,
        "gross_by_resource_usd": (
            {k: str(v) for k, v in by_resource.items()} if not problems else {}
        ),
        "attribution": (
            "sandbox" if objects == {SANDBOX} else "app" if objects == {app_id} else None
        ),
        "app_isolation_proof_required": objects == {app_id},
    }


async def fetch(start, end):
    import modal
    from modal._workspace import _Workspace
    from modal.types import BillingReportItem
    from modal_proto import api_pb2

    if modal.__version__ != "1.5.2":
        raise ValueError("review billing transport before changing the pinned SDK")
    if "app_ids" not in api_pb2.WorkspaceBillingReportRequest.DESCRIPTOR.fields_by_name:
        raise ValueError("app-scoped billing filter unavailable")
    workspace = _Workspace.from_context()
    await workspace.hydrate()
    expected_workspace = json.loads((FOLDER / "receipt.json").read_text())["workspace"]
    if workspace.name != expected_workspace:
        raise ValueError("unexpected active workspace")
    app = await modal.App.lookup.aio(APP, client=workspace.client, create_if_missing=False)
    if not isinstance(app.app_id, str) or not app.app_id.startswith("ap-"):
        raise ValueError("missing dedicated app identity")
    # The public wrapper omits app_ids; use its same read-only RPC with an explicit filter.
    request = api_pb2.WorkspaceBillingReportRequest(
        app_ids=[app.app_id], resolution="h", tag_names=["purpose", "probe"]
    )
    request.start_timestamp.FromDatetime(datetime.fromtimestamp(start, UTC))
    request.end_timestamp.FromDatetime(datetime.fromtimestamp(end, UTC))
    rows = [
        BillingReportItem._from_proto(row)
        async for row in workspace.client.stub.WorkspaceBillingReport.unary_stream(request)
    ]
    return {
        "app_id": app.app_id,
        "sdk": modal.__version__,
        **summarize_rows(rows, app.app_id, start, end),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    source = RESULT.read_bytes()
    start, end, earliest = query_window(json.loads(source))
    record = {
        "sandbox_id": SANDBOX,
        "app_name": APP,
        "result_sha256": hashlib.sha256(source).hexdigest(),
        "fetch_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "query_start_utc": datetime.fromtimestamp(start, UTC).isoformat(),
        "query_end_exclusive_utc": datetime.fromtimestamp(end, UTC).isoformat(),
        "earliest_fetch_utc": datetime.fromtimestamp(earliest, UTC).isoformat(),
        "resolution": "h",
        "collection_buffer_seconds": 180,
        "cost_basis": "gross provider-reported usage before credits and reservations",
        "limitations": "Collection delay has no documented maximum or finality flag. "
        "This fetch cannot certify invoice finality or app isolation. No ledger settlement.",
        "billing_documentation": "https://modal.com/docs/guide/billing",
        "ledger_modified": False,
    }
    if not args.fetch:
        print(json.dumps(record, indent=2))
        return
    if time.time() < earliest:
        raise SystemExit("Refusing early billing fetch; earliest: " + record["earliest_fetch_utc"])
    # Exclusive creation is the permanent once-only claim, even after a failed request.
    with OUTPUT.open("x") as file:
        record.update(status="fetch_started", requested_at_utc=datetime.now(UTC).isoformat())
        file.write(json.dumps(record, indent=2) + "\n")
        file.flush()
        os.fsync(file.fileno())
        try:
            record.update(asyncio.run(fetch(start, end)))
        except Exception as exc:
            record.update(status="fetch_failed_reserve_unchanged", error_type=type(exc).__name__)
        record["finished_at_utc"] = datetime.now(UTC).isoformat()
        file.seek(0)
        file.write(json.dumps(record, indent=2) + "\n")
        file.truncate()
    print(
        json.dumps({"artifact": str(OUTPUT), "status": record["status"], "ledger_modified": False})
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Versioned, bounded, app-scoped billing read. Offline unless --fetch is explicit."""

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
UTC = timezone.utc  # noqa: UP017 -- pinned Modal Python 3.10
TIMEOUT_SECONDS = 60
LOADED_SOURCE_HASHES = {}


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    source = (ROOT / "scripts" / filename).read_bytes()
    exec(compile(source, str(ROOT / "scripts" / filename), "exec"), module.__dict__)
    LOADED_SOURCE_HASHES[filename] = hashlib.sha256(source).hexdigest()
    return module


shared = load("billing_shared_v2", "reconcile_phonellm_probe.py")
episodes = load("billing_episodes_v2", "reconcile_phonellm_episodes.py")
LOADED_SOURCE_HASHES[Path(__file__).name] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def configuration(target):
    validate_loaded_sources()
    if target == "probe":
        folder = ROOT / "runs/v2/phonellm-probe-20260917"
        result_path = folder / "execution/result.json"
        raw = result_path.read_bytes()
        result = json.loads(raw)
        receipt_raw = (folder / "receipt.json").read_bytes()
        receipt = json.loads(receipt_raw)
        if result.get("receipt_sha256") != sha(receipt_raw):
            raise ValueError("original probe receipt binding mismatch")
        if (
            result.get("sandbox_id") != "sb-t7CY44k1Ye28Tb4fMRYECh"
            or receipt.get("reservation_id") != "phonellm-sandbox-probe-20260917-1"
            or result.get("plan_sha256") != receipt.get("plan_sha256")
        ):
            raise ValueError("original probe identity binding mismatch")
        shared.SANDBOX = result["sandbox_id"]
        start, end, earliest = shared.query_window(result)
        interruption_path = folder / "billing-fetch-interruption.json"
        interruption = json.loads(interruption_path.read_text())
        old = folder / "gross-billing-fetch.json"
        old_record = json.loads(old.read_text())
        if (
            interruption.get("terminal_process_observed") is not True
            or interruption["original_fetch_sha256"] != sha(old.read_bytes())
            or json.loads(old.read_text())["status"] != "fetch_started"
        ):
            raise ValueError("original interrupted fetch evidence mismatch")
        if (
            old_record.get("result_sha256") != sha(raw)
            or old_record.get("sandbox_id") != result["sandbox_id"]
            or old_record.get("app_name") != "kitchenrush-phonellm-bounded-probe"
            or old_record.get("query_start_utc") != datetime.fromtimestamp(start, UTC).isoformat()
            or old_record.get("query_end_exclusive_utc")
            != datetime.fromtimestamp(end, UTC).isoformat()
        ):
            raise ValueError("prior billing scope binding mismatch")
        hashes = dict(
            result_sha256=sha(raw),
            prior_interrupted_fetch_sha256=sha(old.read_bytes()),
            interruption_sha256=sha(interruption_path.read_bytes()),
            interruption_path=interruption_path.relative_to(ROOT).as_posix(),
            receipt_sha256=sha(receipt_raw),
        )
        app = "kitchenrush-phonellm-bounded-probe"
        output = folder / "gross-billing-fetch-v2.json"
    elif target == "episodes":
        result, receipt, start, end, earliest, hashes = episodes.evidence()
        app = episodes.APP
        output = episodes.FOLDER / "gross-billing-attempt2-v2.json"
        if episodes.OUTPUT.exists():
            raise ValueError("original episode billing attempt exists; review before new attempt")
    else:
        raise ValueError("unknown target")
    shared.SANDBOX = result["sandbox_id"]
    record = dict(
        kind="bounded_scoped_billing_v2",
        target=target,
        app_name=app,
        sandbox_id=result["sandbox_id"],
        **hashes,
        fetch_script_sha256=LOADED_SOURCE_HASHES[Path(__file__).name],
        helper_sha256={
            f: LOADED_SOURCE_HASHES[f]
            for f in ("reconcile_phonellm_probe.py", "reconcile_phonellm_episodes.py")
        },
        query_start_utc=datetime.fromtimestamp(start, UTC).isoformat(),
        query_end_exclusive_utc=datetime.fromtimestamp(end, UTC).isoformat(),
        earliest_fetch_utc=datetime.fromtimestamp(earliest, UTC).isoformat(),
        operation_timeout_seconds=TIMEOUT_SECONDS,
        cleanup_timeout_seconds=5,
        client_api="native async _Workspace + _App in one asyncio.run loop",
        ledger_modified=False,
        cost_basis="gross provider report before credits/reservations",
        limitations="Reported amounts require attribution and collection review. "
        "No invoice-finality "
        "claim or ledger settlement. Failed/timed-out queries preserve all liabilities.",
    )
    return dict(
        app=app,
        sandbox_id=result["sandbox_id"],
        workspace=receipt["workspace"],
        start=start,
        end=end,
        earliest=earliest,
        output=output,
        record=record,
    )


def validate_loaded_sources():
    for filename, expected in LOADED_SOURCE_HASHES.items():
        if sha((ROOT / "scripts" / filename).read_bytes()) != expected:
            raise ValueError("billing source changed after loading")


async def fetch_native(config, checkpoint=lambda stage: None, *, dependencies=None):
    validate_loaded_sources()
    if dependencies is None:
        import modal
        from modal._workspace import _Workspace
        from modal.app import _App
        from modal.client import _Client
        from modal.types import BillingReportItem
        from modal_proto import api_pb2

        if modal.__version__ != "1.5.2":
            raise ValueError("pinned SDK changed")
        dependencies = (
            _Workspace.from_context,
            _App.lookup,
            api_pb2.WorkspaceBillingReportRequest,
            BillingReportItem._from_proto,
            _Client.from_env,
        )
    workspace_factory, lookup, request_factory, decode = dependencies[:4]
    client_factory = dependencies[4] if len(dependencies) == 5 else None
    client = None
    try:
        if client_factory is not None:
            client = await client_factory()
            workspace = workspace_factory(client=client)
        else:
            workspace = workspace_factory()
            client = getattr(workspace, "client", None)
        checkpoint("workspace_lookup_started")
        await workspace.hydrate()
        client = workspace.client
        if workspace.name != config["workspace"]:
            raise ValueError("unexpected workspace")
        checkpoint("app_lookup_started")
        app = await lookup(config["app"], client=client, create_if_missing=False)
        if not isinstance(app.app_id, str) or not app.app_id.startswith("ap-"):
            raise ValueError("missing app identity")
        request = request_factory(
            app_ids=[app.app_id], resolution="h", tag_names=["purpose", "probe", "experiment"]
        )
        request.start_timestamp.FromDatetime(datetime.fromtimestamp(config["start"], UTC))
        request.end_timestamp.FromDatetime(datetime.fromtimestamp(config["end"], UTC))
        checkpoint("billing_stream_started")
        rows = [
            decode(row) async for row in client.stub.WorkspaceBillingReport.unary_stream(request)
        ]
        checkpoint("billing_stream_finished")
        shared.SANDBOX = config["sandbox_id"]
        return dict(
            app_id=app.app_id,
            sdk="1.5.2",
            **shared.summarize_rows(rows, app.app_id, config["start"], config["end"]),
        )
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client._close(), timeout=5)
            except Exception:
                checkpoint("client_cleanup_failed")


async def bounded_fetch(
    config, checkpoint=lambda stage: None, *, timeout=TIMEOUT_SECONDS, dependencies=None
):
    try:
        return await asyncio.wait_for(
            fetch_native(config, checkpoint, dependencies=dependencies), timeout=timeout
        )
    except asyncio.TimeoutError:  # noqa: UP041 -- distinct from builtin in pinned Python 3.10
        return dict(status="fetch_timeout_reserve_unchanged", error_type="TimeoutError")
    except Exception as exc:
        return dict(status="fetch_failed_reserve_unchanged", error_type=type(exc).__name__)


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("probe", "episodes"), required=True)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    config = configuration(args.target)
    record = config["record"]
    output = config["output"]
    if not args.fetch:
        print(json.dumps(record, indent=2))
        return 0
    if time.time() < config["earliest"]:
        raise SystemExit("billing interval not closed plus collection buffer")
    validate_loaded_sources()
    with output.open("x") as file:
        record.update(status="fetch_started", requested_at_utc=datetime.now(UTC).isoformat())
        file.write(json.dumps(record, indent=2) + "\n")
        file.flush()
        os.fsync(file.fileno())

    def checkpoint(stage):
        record.update(stage=stage, stage_at_utc=datetime.now(UTC).isoformat())
        save(output, record)

    record.update(asyncio.run(bounded_fetch(config, checkpoint)))
    record["finished_at_utc"] = datetime.now(UTC).isoformat()
    save(output, record)
    print(json.dumps(dict(artifact=str(output), status=record["status"], ledger_modified=False)))
    return 0 if record["status"] == "gross_usage_reported_requires_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())

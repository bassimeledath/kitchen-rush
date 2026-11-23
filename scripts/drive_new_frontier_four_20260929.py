#!/usr/bin/env python3
"""Bounded seed-by-seed dispatcher for the frozen four-model episode evaluator."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_new_frontier_four_20260929 as run  # noqa: E402
from kitchenrush.v2.providers import load_env  # noqa: E402

DRIVER_WALL_SECONDS = 12 * 3600


def save_driver_manifest():
    path = run.RUNS / "execution-driver.json"
    expected = {"file": str(Path(__file__).relative_to(ROOT)),
                "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "scoring_protocol_hash": run.check_manifest()}
    if path.exists():
        if json.loads(path.read_text()) != expected:
            raise RuntimeError("execution driver changed after run start")
    else:
        run.save(path, expected)


def main():
    save_driver_manifest()
    load_env(ROOT / ".env")
    ledger = run.CampaignLedger(run.RUNS / "spend.sqlite", cap=run.CAMPAIGN["inference_ceiling_usd"])
    snapshot = ledger.snapshot()
    if snapshot["unresolved_requests"] or snapshot["overruns"]:
        raise RuntimeError("ledger needs audit")
    for key in run.MODELS:
        path = run.RUNS / "canaries" / f"{run.NAME}-canary-{key}.json"
        if json.loads(path.read_text())["status"] != "complete":
            raise RuntimeError(f"canary failed: {key}")
    started = time.monotonic()
    parked = set()
    failures = {key: 0 for key in run.MODELS}
    for level in run.LEVELS:
        run.env.WAITS = run.HARD_WAITS if level == "hard" else run.NORMAL_WAITS
        for row in run.rows(level):
            if time.monotonic() - started >= DRIVER_WALL_SECONDS:
                print(json.dumps({"stopped": "driver_wall_gate", "before": [level, row["seed"]]}), flush=True)
                return
            keys = [key for key in run.MODELS if key not in parked]
            if not keys:
                print(json.dumps({"stopped": "all_configurations_parked"}), flush=True)
                return
            # Only one independent kitchen per configuration is in flight. This
            # makes the predeclared failure rule effective before the next seed.
            with ThreadPoolExecutor(max_workers=run.CAMPAIGN["maximum_parallel_episodes"]) as pool:
                futures = {pool.submit(run.run_episode, level, row, key, ledger, run.check_manifest()): key
                           for key in keys}
                results = []
                for future in as_completed(futures):
                    key = futures[future]
                    ep = future.result()
                    results.append((key, ep))
                    print(json.dumps({"level": level, "seed": row["seed"], "key": key,
                                      "status": ep["status"], "served": ep["completed"],
                                      "decisions": ep["decisions"], "cost_usd": ep["cost_usd"],
                                      "reason": ep["reason"]}), flush=True)
            stop = None
            for key, ep in results:
                if ep["status"] == "quarantined":
                    failures[key] += 1
                    details = ep["requests"][-1].get("transport_failure", {}) if ep["requests"] else {}
                    if details.get("kind") == "http_error" and details.get("status") in (400, 401, 402, 403):
                        stop = f"configuration_http_{details['status']}"
                    if failures[key] >= 2:
                        parked.add(key)
                elif ep["status"] == "incomplete":
                    stop = "operational_stop"
                else:
                    failures[key] = 0
            snapshot = ledger.snapshot()
            if snapshot["unresolved_requests"] or snapshot["overruns"]:
                stop = "ledger_audit_required"
            if stop:
                print(json.dumps({"stopped": stop, "after": [level, row["seed"]],
                                  "parked": sorted(parked), "ledger": snapshot}), flush=True)
                return
        print(json.dumps({"level_finished": level, "parked": sorted(parked),
                          "ledger": ledger.snapshot()}), flush=True)
    print(json.dumps({"campaign_finished": True, "ledger": ledger.snapshot()}), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fixed continuation of every unscored four-model assignment after provider failures."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_new_frontier_four_20260929 as prior  # noqa: E402
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2.ledger import BudgetExceeded  # noqa: E402
from kitchenrush.v2.providers import ProviderError, load_env  # noqa: E402

NAME = "new-frontier-four-recovery-20260930"
DATA = ROOT / "data/v2" / NAME
RUNS = ROOT / "runs/v2" / NAME
CAMPAIGN = json.loads((DATA / "campaign.json").read_text())
ASSIGNMENTS = json.loads((DATA / "assignments.json").read_text())
DRIVER_WALL_SECONDS = 12 * 3600


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files():
    paths = [Path(__file__), DATA / "campaign.json", DATA / "assignments.json",
             prior.RUNS / "source-manifest.json", prior.RUNS / "audit.json",
             prior.RUNS / "execution-driver.json"]
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def check_manifest(write=False):
    prior_hash = prior.check_manifest()
    if prior_hash != CAMPAIGN["original_protocol_hash"]:
        raise RuntimeError("original scoring protocol changed")
    original_audit = prior.RUNS / "audit.json"
    if digest(original_audit) != CAMPAIGN["original_audit_sha256"]:
        raise RuntimeError("original audit changed")
    if (CAMPAIGN["original_accounted_usd"] + CAMPAIGN["inference_ceiling_usd"]
            > CAMPAIGN["combined_with_original_accounted_ceiling_usd"]):
        raise RuntimeError("combined cap exceeds authorization")
    expected = {"protocol": "fixed-all-missing-assignment-continuation-1",
                "original_protocol_hash": prior_hash, "files": files()}
    expected["protocol_hash"] = prior.base.digest(expected)
    path = RUNS / "source-manifest.json"
    if path.exists():
        if json.loads(path.read_text()) != expected:
            raise RuntimeError("continuation source changed after start")
    elif write:
        prior.save(path, expected)
    return expected["protocol_hash"]


def validate_assignments():
    assert len(ASSIGNMENTS) == CAMPAIGN["assignments"] == 70
    prior_audit = json.loads((prior.RUNS / "audit.json").read_text())
    scored = {(e["level"], e["seed"], e["key"])
              for e in prior_audit["episodes"] if e["scored"]}
    expected = {(level, row["seed"], key)
                for level in prior.LEVELS for row in prior.rows(level) for key in prior.MODELS}
    missing = expected - scored
    got = {(a["level"], a["seed"], a["key"]) for a in ASSIGNMENTS}
    assert got == missing and len(got) == len(ASSIGNMENTS)
    for a in ASSIGNMENTS:
        original = prior.RUNS / "episodes" / f"{prior.NAME}-{a['key']}-{a['level']}-{a['seed']}.json"
        if a["original_status"] == "unattempted":
            assert a["original_sha256"] is None and not original.exists()
        else:
            assert original.exists() and digest(original) == a["original_sha256"]
            assert json.loads(original.read_text())["status"] == a["original_status"]


def run_episode(a, ledger, protocol_hash):
    level, seed, key = a["level"], a["seed"], a["key"]
    eid = f"{NAME}-{key}-{level}-{seed}"
    path = RUNS / "episodes" / f"{eid}.json"
    if path.exists():
        ep = json.loads(path.read_text())
        if ep["status"] == "running" or ep["protocol_hash"] != protocol_hash:
            raise RuntimeError(f"interrupted or mismatched continuation: {path}")
        return ep
    if ledger.used(eid):
        raise RuntimeError("charged continuation without artifact")
    row = next(r for r in prior.rows(level) if r["seed"] == seed)
    engine = prior.engine_for(level, row)
    model = prior.MODELS[key]
    client = prior.transport.BoundedStreamingClient(
        model, total_timeout=CAMPAIGN["request_deadline_seconds"])
    ep = {"id": eid, "protocol": prior.PROTOCOL,
          "continuation_protocol": "fixed-all-missing-assignment-continuation-1",
          "protocol_hash": protocol_hash, "original_protocol_hash": prior.check_manifest(),
          "original_status": a["original_status"], "original_sha256": a["original_sha256"],
          "model": asdict(model), "level": level, "seed": seed,
          "spec": row["spec"], "spec_hash": prior.base.digest(row["spec"]),
          "feasibility_witness_hash": row["witness_hash"],
          "status": "running", "actions": [], "requests": [],
          "input_tokens": 0, "output_tokens": 0, "accepted_cost_usd": 0,
          "dollar_cap": CAMPAIGN["episode_ceiling_usd"], "rank_eligible": False}
    prior.save(path, ep)
    started = time.monotonic()
    reason, status = "game_terminal", "complete"
    while not engine.terminated and len(ep["actions"]) < CAMPAIGN["maximum_decisions_per_episode"]:
        if time.monotonic() - started >= CAMPAIGN["episode_wall_seconds"]:
            reason, status = "operational_wall_gate", "incomplete"
            break
        budget = {"kind": "USD", "allowance": ep["dollar_cap"],
                  "remaining": max(0, ep["dollar_cap"] - ledger.used(eid)),
                  "decisions_remaining": CAMPAIGN["maximum_decisions_per_episode"] - len(ep["actions"])}
        canonical, options = prior.base.decision(prior.base.public_state(engine), budget)
        try:
            req, choice = prior.dispatch(client, canonical, eid, "main", ledger, ep, path,
                                         ep["dollar_cap"])
            req["selection"] = choice
            selected = options.get(choice) if isinstance(choice, str) else None
            action = selected if selected is not None else ToolCall("invalid_model_selection", {})
            engine.step([action])
            ep["actions"].append(asdict(action))
            req["admitted"] = True
            ep["accepted_cost_usd"] += req["cost_usd"]
            prior.save(path, ep)
        except BudgetExceeded as error:
            ep["error"] = str(error)
            reason, status = "operational_spending_gate", "incomplete"
            break
        except RuntimeError as error:
            if str(error) == "prompt_ceiling":
                reason, status = "prompt_ceiling", "incomplete"
            else:
                ep["error"] = f"{type(error).__name__}: {error}"
                reason, status = "provider_or_accounting_failure", "quarantined"
            break
        except (ProviderError, ValueError, KeyError) as error:
            ep["error"] = f"{type(error).__name__}: {error}"
            reason, status = "provider_or_accounting_failure", "quarantined"
            break
    if not engine.terminated and status == "complete":
        reason = "decision_limit"
    ep.update(reason=reason, status=status, completed=engine.counters["orders_served"],
              invalid=engine.counters["invalid_actions"], quality=prior.base.quality(engine),
              decisions=len(ep["actions"]), clock_gs=engine.clock_gs,
              cost_usd=ledger.used(eid), rank_eligible=status == "complete")
    prior.save(path, ep)
    return ep


def dry_run():
    validate_assignments()
    check_manifest(write=False)
    assert CAMPAIGN["maximum_parallel_episodes"] == 2
    for a in ASSIGNMENTS:
        row = next(r for r in prior.rows(a["level"]) if r["seed"] == a["seed"])
        prior.env.WAITS = prior.HARD_WAITS if a["level"] == "hard" else prior.NORMAL_WAITS
        engine = prior.engine_for(a["level"], row)
        canonical, options = prior.base.decision(
            prior.base.public_state(engine), prior.budget_state(engine, a["level"]))
        assert options
        client = prior.transport.BoundedStreamingClient(prior.MODELS[a["key"]])
        client.bound(client.payload(canonical))
    print(json.dumps({"dry_run": "passed", "assignments": len(ASSIGNMENTS)}))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "dry-run":
        dry_run()
        return
    validate_assignments()
    protocol_hash = check_manifest(write=True)
    load_env(ROOT / ".env")
    ledger = prior.CampaignLedger(RUNS / "spend.sqlite",
                                  cap=CAMPAIGN["inference_ceiling_usd"])
    snapshot = ledger.snapshot()
    if snapshot["unresolved_requests"] or snapshot["overruns"]:
        raise RuntimeError("continuation ledger needs audit")
    started = time.monotonic()
    parked = set()
    failures = {key: 0 for key in prior.MODELS}
    grouped = {}
    for a in ASSIGNMENTS:
        grouped.setdefault((a["level"], a["seed"]), []).append(a)
    for (level, seed), group in grouped.items():
        prior.env.WAITS = prior.HARD_WAITS if level == "hard" else prior.NORMAL_WAITS
        if time.monotonic() - started >= DRIVER_WALL_SECONDS:
            print(json.dumps({"stopped": "driver_wall_gate", "before": [level, seed]}), flush=True)
            return
        for effort in ("low", "high"):
            batch = [a for a in group if a["key"] not in parked
                     and a["key"].endswith("-" + effort)]
            if not batch:
                continue
            assert len(batch) <= 2 and len({prior.MODELS[a["key"]].provider for a in batch}) == len(batch)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(run_episode, a, ledger, protocol_hash): a for a in batch}
                results = []
                for future in as_completed(futures):
                    a = futures[future]
                    ep = future.result()
                    results.append((a, ep))
                    print(json.dumps({"level": level, "seed": seed, "key": a["key"],
                                      "status": ep["status"], "served": ep["completed"],
                                      "decisions": ep["decisions"], "cost_usd": ep["cost_usd"],
                                      "reason": ep["reason"]}), flush=True)
            stop = None
            for a, ep in results:
                key = a["key"]
                if ep["status"] == "quarantined":
                    failures[key] += 1
                    details = ep["requests"][-1].get("transport_failure", {}) if ep["requests"] else {}
                    provider_error = details.get("provider_error") or {}
                    if str(provider_error.get("code")) == "429":
                        stop = "upstream_429"
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
                print(json.dumps({"stopped": stop, "after": [level, seed],
                                  "parked": sorted(parked), "ledger": snapshot}), flush=True)
                return
        print(json.dumps({"seed_finished": [level, seed], "parked": sorted(parked)}), flush=True)
    print(json.dumps({"continuation_finished": True, "parked": sorted(parked),
                      "ledger": ledger.snapshot()}), flush=True)


if __name__ == "__main__":
    main()

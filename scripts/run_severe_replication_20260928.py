#!/usr/bin/env python3
"""Fixed 12-pair native severe replication, with independent atomic spend ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

import continue_extreme_sol_20260926 as prior_stream
import extreme_sol_kitchens_20260926 as kitchens
import run_diverse_complexity_pilot_20260926 as base
from kitchenrush.tools import ToolCall
from kitchenrush.v2.ledger import BudgetExceeded, Ledger, units
from kitchenrush.v2.providers import Model, ProviderError, load_env

ROOT = Path(__file__).resolve().parents[1]
NAME = "severe-replication-20260928"
DATA = ROOT / "data/v2" / NAME
RUNS = ROOT / "runs/v2" / NAME
CAMPAIGN = DATA / "campaign.json"
KITCHENS = DATA / "kitchens.json"
MODEL_SOURCE = ROOT / "data/v2/extreme-sol-reasoning-r2-20260926/roster.json"
WALL_LIMIT_SECONDS = 3600
CAMPAIGN_WALL_SECONDS = 8 * 3600
PROTOCOL = "kitchen-rush-native-single-chef-severe-fixed-replication-1"
ORDER = ("gpt-6-sol-low", "gpt-6-sol-high")
ROSTER = json.loads(MODEL_SOURCE.read_text())
MODELS = {entry["model"]["key"]: Model(**entry["model"])
          for entry in ROSTER["configurations"]}
CAPS = {entry["model"]["key"]: entry["episode_cap_usd"]
        for entry in ROSTER["configurations"]}


def model_request_reserve(model):
    return (model.prompt_limit * (model.reservation_input_price or model.input_price)
            + model.max_output * (model.reservation_output_price or model.output_price)
            + model.request_fee)


def source_files():
    paths = [Path(__file__), ROOT / "scripts/build_severe_replication_20260928.py",
             ROOT / "scripts/extreme_sol_kitchens_20260926.py",
             ROOT / "scripts/continue_extreme_sol_20260926.py",
             ROOT / "scripts/run_extreme_sol_20260926.py",
             ROOT / "scripts/run_diverse_complexity_pilot_20260926.py",
             ROOT / "scripts/complexity_ladder_v2.py",
             ROOT / "scripts/difficulty_sweep_v2.py",
             ROOT / "scripts/concurrency_probe_v2.py",
             ROOT / "scripts/tune_probe_v2.py", MODEL_SOURCE,
             CAMPAIGN, KITCHENS, DATA / "exclusions.json"]
    paths += sorted((ROOT / "src/kitchenrush").rglob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def protocol_hash():
    return base.digest({"protocol": PROTOCOL, "files": source_files()})


def save(path, data):
    base.write_json(path, data)


def finish(ep, engine, ledger, path, reason, status):
    return base.finish(ep, engine, ledger, path, reason, status)


def run_episode(row, model, ledger, frozen_hash):
    eid = f"{NAME}-{model.key}-level-4-{row['seed']}"
    path = RUNS / "episodes" / f"{eid}.json"
    if path.exists():
        old = json.loads(path.read_text())
        if old["status"] == "running":
            raise RuntimeError(f"interrupted episode needs audit: {path}")
        if old["protocol_hash"] != frozen_hash:
            raise RuntimeError("saved episode protocol hash differs")
        return old
    if ledger.used(eid):
        raise RuntimeError("ledger contains charges but no episode artifact")
    cap = CAPS[model.key]
    engine = base.VariantEngine(base.deserialize_spec(row["spec"]), record_trace=True)
    client = prior_stream.StreamingClient(model, timeout=600)
    ep = {"id": eid, "protocol": PROTOCOL, "protocol_hash": frozen_hash,
          "base_protocol_hash": base.protocol_hash(), "model": asdict(model),
          "spec": row["spec"], "spec_hash": base.digest(row["spec"]),
          "level": 4, "seed": row["seed"], "setting": row["setting"],
          "feasibility_witness_hash": row["witness_hash"],
          "certificate": row["capacity_certificate"], "dollar_cap": cap,
          "canary": False, "status": "running", "actions": [], "requests": [],
          "input_tokens": 0, "output_tokens": 0, "accepted_cost_usd": 0,
          "rank_eligible": False, "accounting_contract": "separate-atomic-campaign-ledger-1"}
    path.parent.mkdir(parents=True, exist_ok=True)
    save(path, ep)
    started = time.monotonic()
    while not engine.terminated and len(ep["actions"]) < base.MAX_DECISIONS:
        if time.monotonic() - started >= WALL_LIMIT_SECONDS:
            return finish(ep, engine, ledger, path, "operational_wall_gate", "incomplete")
        remaining = max(0, cap - ledger.used(eid))
        budget = {"kind": "USD", "allowance": cap, "remaining": remaining,
                  "decisions_remaining": base.MAX_DECISIONS - len(ep["actions"])}
        canonical, options = base.decision(base.public_state(engine), budget)
        payload = client.payload(canonical)
        if len(json.dumps(payload, ensure_ascii=True).encode()) + 512 > model.prompt_limit:
            return finish(ep, engine, ledger, path, "prompt_ceiling", "incomplete")
        prompt_bound, bound = client.bound(payload)
        rid = f"{eid}:{len(ep['requests'])}"
        try:
            ledger.reserve(rid, eid, "main", bound, episode_cap=cap)
        except BudgetExceeded as error:
            ep["gate_error"] = str(error)
            return finish(ep, engine, ledger, path, "operational_spending_gate", "incomplete")
        request = {"id": rid, "payload": payload,
                   "reserved_usd": bound, "admitted": False}
        ep["requests"].append(request)
        save(path, ep)
        request_started = time.monotonic()
        try:
            response = client.evaluate(payload)
            request["response"] = response
            n_in, n_out, cost = client.usage(response, payload)
            request.update(input_tokens=n_in, output_tokens=n_out, cost_usd=cost,
                           wall_seconds=time.monotonic() - request_started)
            ep["input_tokens"] += n_in
            ep["output_tokens"] += n_out
            ledger.settle(rid, cost)
            save(path, ep)
            client.validate_identity(response)
            if n_in > prompt_bound or n_out > payload["max_tokens"]:
                raise ProviderError("token reservation bound violated")
            choice = client.select(response)
            call = options.get(choice) if isinstance(choice, str) else None
            request["selection"] = choice
            selected = call if call is not None else ToolCall("invalid_model_selection", {})
            engine.step([selected])
            ep["actions"].append(asdict(selected))
            request["admitted"] = True
            ep["accepted_cost_usd"] += cost
            save(path, ep)
        except (ProviderError, RuntimeError, ValueError, KeyError) as error:
            with ledger.connect() as con:
                actual = con.execute("SELECT actual FROM requests WHERE id=?", (rid,)).fetchone()[0]
            if actual is None:
                ledger.uncertain(rid)
                ledger.retain_failed_liability(rid)
            ep["error"] = f"{type(error).__name__}: {error}"
            return finish(ep, engine, ledger, path, "provider_or_accounting_failure", "quarantined")
    return finish(ep, engine, ledger, path,
                  "game_terminal" if engine.terminated else "decision_limit", "complete")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    campaign = json.loads(CAMPAIGN.read_text())
    assert campaign["seeds"] == [90603, 90604, 90605, 90606, 90607, 90608,
                                 90609, 90611, 90612, 90613, 90614, 90615]
    assert campaign["models"] == list(ORDER)
    assert campaign["episode_cap_usd"] == CAPS[ORDER[0]] == CAPS[ORDER[1]] == 3.25
    assert campaign["episode_wall_seconds"] == WALL_LIMIT_SECONDS
    assert campaign["campaign_wall_seconds"] == CAMPAIGN_WALL_SECONDS
    rows = json.loads(KITCHENS.read_text())
    assert [r["seed"] for r in rows] == campaign["seeds"]
    for row in rows:
        kitchens.verify(row)
    frozen_hash = protocol_hash()
    ledger = Ledger(RUNS / "spend.sqlite", cap=campaign["campaign_ceiling_usd"])
    snapshot = ledger.snapshot()
    if snapshot["unresolved_requests"] or snapshot["overruns"]:
        raise RuntimeError("new campaign ledger needs audit")
    manifest = RUNS / "source-manifest.json"
    expected = {"protocol_hash": frozen_hash, "files": source_files()}
    if manifest.exists():
        assert json.loads(manifest.read_text()) == expected
    elif args.run:
        save(manifest, expected)
    print(json.dumps({"protocol_hash": frozen_hash, "campaign": campaign,
                      "ledger": snapshot}), flush=True)
    if not args.run:
        return
    load_env(ROOT / ".env")
    start_path = RUNS / "campaign-start.json"
    if start_path.exists():
        campaign_started = json.loads(start_path.read_text())["started_unix_seconds"]
    else:
        campaign_started = time.time()
        save(start_path, {"started_unix_seconds": campaign_started})
    for row in rows:
        for key in ORDER:
            path = RUNS / "episodes" / f"{NAME}-{key}-level-4-{row['seed']}.json"
            if time.time() - campaign_started >= CAMPAIGN_WALL_SECONDS and not path.exists():
                print(json.dumps({"stopped": "campaign_wall_gate", "before": [row["seed"], key]}), flush=True)
                return
            ep = run_episode(row, MODELS[key], ledger, frozen_hash)
            print(json.dumps({k: ep.get(k) for k in
                              ("id", "status", "reason", "completed", "invalid",
                               "decisions", "output_tokens", "cost_usd")}), flush=True)
            if ep["reason"] == "operational_spending_gate":
                # An episode fuse is unscored, but the other fixed episodes still run.
                # A campaign fuse stops the entire replication.
                if units(ledger.used()) + units(model_request_reserve(MODELS[key])) > units(campaign["campaign_ceiling_usd"]):
                    print(json.dumps({"stopped": "campaign_spending_gate",
                                      "ledger": ledger.snapshot()}), flush=True)
                    return
    print(json.dumps({"finished": True, "ledger": ledger.snapshot()}), flush=True)


if __name__ == "__main__":
    main()

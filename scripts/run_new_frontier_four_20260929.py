#!/usr/bin/env python3
"""Bounded four-configuration run on the frozen Easy, Medium and Hard banks."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_diverse_complexity_pilot_20260926 as base  # noqa: E402
import severe_recovery_transport_20260929 as transport  # noqa: E402
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2 import environment as env  # noqa: E402
from kitchenrush.v2.ledger import BudgetExceeded, Ledger, units  # noqa: E402
from kitchenrush.v2.providers import Model, ProviderError, load_env  # noqa: E402

NAME = "new-frontier-four-20260929"
DATA = ROOT / "data/v2" / NAME
RUNS = ROOT / "runs/v2" / NAME
BANKS = ROOT / "data/v2/three-level-release-20260929"
CAMPAIGN = json.loads((DATA / "campaign.json").read_text())
PROTOCOL = "single-chef-frozen-three-level-four-new-model-efforts-1"
LEVELS = ("easy", "medium", "hard")
NORMAL_WAITS = (1, 2, 5, 10, 20)
HARD_WAITS = (1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150)
PRICE_IN = 2e-6
PRICE_OUT = 10e-6
MAX_OUTPUT = CAMPAIGN["max_output_tokens_per_request"]
MAX_PROMPT = CAMPAIGN["maximum_prompt_bytes"]
EPISODE_CAP = CAMPAIGN["episode_ceiling_usd"]
MODELS = {
    cfg["key"]: Model(
        key=cfg["key"], model=cfg["model"], provider=cfg["provider"],
        route_slug=cfg["route_slug"], reasoning={"effort": cfg["effort"]},
        input_price=PRICE_IN, output_price=PRICE_OUT,
        max_output=MAX_OUTPUT, prompt_limit=MAX_PROMPT,
        reservation_input_price=4e-6, reservation_output_price=15e-6,
        reservation_basis="twice_standard_input_and_1_5x_standard_output",
    ) for cfg in CAMPAIGN["configurations"]
}


class CampaignLedger(Ledger):
    """Atomic global and per-episode caps without legacy stage sublimits."""

    def reserve(self, rid, episode, stage, maximum, episode_cap=None):
        if stage not in ("canary", "main"):
            raise ValueError("unapproved stage")
        amount = units(maximum)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM requests WHERE id=?", (rid,)).fetchone():
                raise ValueError("request already reserved")
            if con.execute("SELECT 1 FROM requests WHERE status IN ('uncertain','overrun')").fetchone():
                raise BudgetExceeded("another request needs an accounting audit")
            if self._used(con) + amount > self.cap:
                raise BudgetExceeded("campaign ceiling")
            if episode_cap is not None and self._used(con, "WHERE episode=?", (episode,)) + amount > units(episode_cap):
                raise BudgetExceeded("episode ceiling")
            if stage == "canary" and self._used(con, "WHERE stage='canary'") + amount > units(CAMPAIGN["canary_ceiling_usd"]):
                raise BudgetExceeded("canary ceiling")
            con.execute("INSERT INTO requests VALUES (?,?,?,?,NULL,?)", (rid, episode, stage, amount, "reserved"))


def save(path: Path, value):
    base.write_json(path, value)


def source_files():
    paths = [Path(__file__), DATA / "campaign.json",
             ROOT / "scripts/severe_recovery_transport_20260929.py",
             ROOT / "scripts/continue_extreme_sol_20260926.py",
             ROOT / "scripts/run_diverse_complexity_pilot_20260926.py",
             ROOT / "scripts/difficulty_sweep_v2.py",
             ROOT / "scripts/freeze_three_level_release_20260929.py",
             ROOT / "scripts/overlap_gap_v2.py",
             ROOT / "scripts/extreme_sol_kitchens_20260926.py",
             BANKS / "source-manifest.json"]
    paths += [BANKS / f"{level}.json" for level in LEVELS]
    paths += sorted((ROOT / "src/kitchenrush").rglob("*.py"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def frozen_hash():
    return base.digest({"protocol": PROTOCOL, "files": source_files()})


def check_manifest(write=False):
    path = RUNS / "source-manifest.json"
    expected = {"protocol": PROTOCOL, "protocol_hash": frozen_hash(), "files": source_files()}
    if path.exists():
        if json.loads(path.read_text()) != expected:
            raise RuntimeError("frozen source changed after campaign start")
    elif write:
        save(path, expected)
    return expected["protocol_hash"]


def rows(level):
    return json.loads((BANKS / f"{level}.json").read_text())


def engine_for(level, row):
    spec = env.deserialize_spec(row["spec"])
    if level == "easy":
        return env.Engine(spec, record_trace=True)
    return base.VariantEngine(spec, record_trace=True)


def budget_state(engine, level):
    return {"kind": "USD", "allowance": EPISODE_CAP,
            "remaining": EPISODE_CAP,
            "decisions_remaining": CAMPAIGN["maximum_decisions_per_episode"] - len(engine.trace)}


def dispatch(client, canonical, eid, stage, ledger, record, path, cap):
    payload = client.payload(canonical)
    if len(json.dumps(payload, ensure_ascii=True).encode()) + 512 > MAX_PROMPT:
        raise RuntimeError("prompt_ceiling")
    prompt_bound, bound = client.bound(payload)
    rid = f"{eid}:{len(record['requests'])}"
    ledger.reserve(rid, eid, stage, bound, episode_cap=cap)
    req = {"id": rid, "payload": payload, "reserved_usd": bound, "admitted": False}
    record["requests"].append(req)
    save(path, record)
    started = time.monotonic()
    try:
        response = client.evaluate(payload)
        req["response"] = response
        n_in, n_out, cost = client.usage(response, payload)
        req.update(input_tokens=n_in, output_tokens=n_out, cost_usd=cost,
                   wall_seconds=time.monotonic() - started)
        record["input_tokens"] += n_in
        record["output_tokens"] += n_out
        ledger.settle(rid, cost)
        save(path, record)
        client.validate_identity(response)
        if n_in > prompt_bound or n_out > payload["max_tokens"]:
            raise ProviderError("token reservation bound violated")
        return req, client.select(response)
    except (ProviderError, RuntimeError, ValueError, KeyError) as error:
        if isinstance(error, transport.TransportFailure):
            req["transport_failure"] = error.details
        with ledger.connect() as con:
            actual = con.execute("SELECT actual FROM requests WHERE id=?", (rid,)).fetchone()[0]
        if actual is None:
            ledger.uncertain(rid)
            ledger.retain_failed_liability(rid)
        record["error"] = f"{type(error).__name__}: {error}"
        save(path, record)
        raise


def run_canary(key, ledger, protocol_hash):
    eid = f"{NAME}-canary-{key}"
    path = RUNS / "canaries" / f"{eid}.json"
    if path.exists():
        prior = json.loads(path.read_text())
        if prior["status"] != "complete" or prior["protocol_hash"] != protocol_hash:
            raise RuntimeError(f"canary needs audit: {path}")
        return prior
    if ledger.used(eid):
        raise RuntimeError("charged canary without artifact")
    env.WAITS = HARD_WAITS
    row = rows("hard")[0]
    engine = engine_for("hard", row)
    canonical, options = base.decision(base.public_state(engine), budget_state(engine, "hard"))
    record = {"id": eid, "model": asdict(MODELS[key]), "protocol_hash": protocol_hash,
              "status": "running", "requests": [], "input_tokens": 0, "output_tokens": 0}
    save(path, record)
    client = transport.BoundedStreamingClient(MODELS[key], total_timeout=CAMPAIGN["request_deadline_seconds"])
    try:
        req, choice = dispatch(client, canonical, eid, "canary", ledger, record, path, 2.0)
        if choice not in options:
            raise ProviderError("canary did not select an offered action")
        req["selection"] = choice
        req["admitted"] = True
        record["status"] = "complete"
    except Exception as error:
        record["status"] = "quarantined"
        record["error"] = f"{type(error).__name__}: {error}"
    record["cost_usd"] = ledger.used(eid)
    save(path, record)
    return record


def run_episode(level, row, key, ledger, protocol_hash):
    eid = f"{NAME}-{key}-{level}-{row['seed']}"
    path = RUNS / "episodes" / f"{eid}.json"
    if path.exists():
        prior = json.loads(path.read_text())
        if prior["status"] == "running" or prior["protocol_hash"] != protocol_hash:
            raise RuntimeError(f"interrupted or mismatched episode needs audit: {path}")
        return prior
    if ledger.used(eid):
        raise RuntimeError("charged episode without artifact")
    engine = engine_for(level, row)
    model = MODELS[key]
    client = transport.BoundedStreamingClient(model, total_timeout=CAMPAIGN["request_deadline_seconds"])
    ep = {"id": eid, "protocol": PROTOCOL, "protocol_hash": protocol_hash,
          "model": asdict(model), "level": level, "seed": row["seed"],
          "spec": row["spec"], "spec_hash": base.digest(row["spec"]),
          "feasibility_witness_hash": row["witness_hash"],
          "status": "running", "actions": [], "requests": [],
          "input_tokens": 0, "output_tokens": 0, "accepted_cost_usd": 0,
          "dollar_cap": EPISODE_CAP, "rank_eligible": False}
    save(path, ep)
    started = time.monotonic()
    reason, status = "game_terminal", "complete"
    while not engine.terminated and len(ep["actions"]) < CAMPAIGN["maximum_decisions_per_episode"]:
        if time.monotonic() - started >= CAMPAIGN["episode_wall_seconds"]:
            reason, status = "operational_wall_gate", "incomplete"
            break
        budget = {"kind": "USD", "allowance": EPISODE_CAP,
                  "remaining": max(0, EPISODE_CAP - ledger.used(eid)),
                  "decisions_remaining": CAMPAIGN["maximum_decisions_per_episode"] - len(ep["actions"])}
        canonical, options = base.decision(base.public_state(engine), budget)
        try:
            req, choice = dispatch(client, canonical, eid, "main", ledger, ep, path, EPISODE_CAP)
            req["selection"] = choice
            selected = options.get(choice) if isinstance(choice, str) else None
            action = selected if selected is not None else ToolCall("invalid_model_selection", {})
            engine.step([action])
            ep["actions"].append(asdict(action))
            req["admitted"] = True
            ep["accepted_cost_usd"] += req["cost_usd"]
            save(path, ep)
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
              invalid=engine.counters["invalid_actions"], quality=base.quality(engine),
              decisions=len(ep["actions"]), clock_gs=engine.clock_gs,
              cost_usd=ledger.used(eid), rank_eligible=status == "complete")
    save(path, ep)
    return ep


def dry_run():
    import freeze_three_level_release_20260929 as freeze
    freeze.verify_banks()
    assert len(MODELS) == 4 and CAMPAIGN["maximum_assignments"] == 144
    for level in LEVELS:
        env.WAITS = HARD_WAITS if level == "hard" else NORMAL_WAITS
        for row in rows(level):
            engine = engine_for(level, row)
            canonical, options = base.decision(base.public_state(engine), budget_state(engine, level))
            assert options
            for model in MODELS.values():
                client = transport.BoundedStreamingClient(model)
                payload = client.payload(canonical)
                assert payload["reasoning"]["effort"] in ("low", "high")
                assert len(json.dumps(payload, ensure_ascii=True).encode()) + 512 <= MAX_PROMPT
                client.bound(payload)
    print(json.dumps({"dry_run": "passed", "banks": 36, "configurations": list(MODELS)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("dry-run", "canaries", "run"))
    args = parser.parse_args()
    if args.mode == "dry-run":
        dry_run()
        return
    protocol_hash = check_manifest(write=True)
    load_env(ROOT / ".env")
    ledger = CampaignLedger(RUNS / "spend.sqlite", cap=CAMPAIGN["inference_ceiling_usd"])
    if ledger.snapshot()["unresolved_requests"] or ledger.snapshot()["overruns"]:
        raise RuntimeError("ledger needs accounting audit")
    if args.mode == "canaries":
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(run_canary, key, ledger, protocol_hash): key for key in MODELS}
            for future in as_completed(futures):
                result = future.result()
                print(json.dumps({"id": result["id"], "status": result["status"],
                                  "error": result.get("error"), "cost_usd": result["cost_usd"]}), flush=True)
        print(json.dumps({"ledger": ledger.snapshot()}), flush=True)
        return
    for key in MODELS:
        path = RUNS / "canaries" / f"{NAME}-canary-{key}.json"
        if not path.exists() or json.loads(path.read_text()).get("status") != "complete":
            raise RuntimeError(f"canary missing or failed: {key}")
    parked: set[str] = set()
    failure_count = {key: 0 for key in MODELS}
    for level in LEVELS:
        env.WAITS = HARD_WAITS if level == "hard" else NORMAL_WAITS
        assignments = [(row, key) for row in rows(level) for key in MODELS if key not in parked]
        with ThreadPoolExecutor(max_workers=CAMPAIGN["maximum_parallel_episodes"]) as pool:
            futures = {pool.submit(run_episode, level, row, key, ledger, protocol_hash): (row["seed"], key)
                       for row, key in assignments}
            for future in as_completed(futures):
                seed, key = futures[future]
                ep = future.result()
                print(json.dumps({"level": level, "seed": seed, "model": key,
                                  "status": ep["status"], "completed": ep["completed"],
                                  "decisions": ep["decisions"], "cost_usd": ep["cost_usd"],
                                  "reason": ep["reason"]}), flush=True)
                if ep["status"] == "quarantined":
                    failure_count[key] += 1
                    failure = ep["requests"][-1].get("transport_failure", {}) if ep["requests"] else {}
                    if failure.get("kind") == "http_error" and failure.get("status") in (400, 401, 402, 403):
                        raise RuntimeError(f"persistent HTTP {failure['status']} on {key}")
                    if failure_count[key] >= 2:
                        parked.add(key)
                elif ep["status"] == "incomplete":
                    raise RuntimeError(f"operational stop on {key} level {level} seed {seed}")
        print(json.dumps({"level_complete": level, "parked": sorted(parked),
                          "ledger": ledger.snapshot()}), flush=True)
    print(json.dumps({"campaign_finished": True, "ledger": ledger.snapshot()}), flush=True)


if __name__ == "__main__":
    main()

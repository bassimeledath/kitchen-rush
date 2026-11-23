#!/usr/bin/env python3
"""Small resumable OpenRouter pilot, with atomic campaign/episode reservations."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from kitchenrush.tools import ToolCall
from kitchenrush.v2.environment import (MAX_DECISIONS, decision, deserialize_spec, digest,
                                       protocol_hash, public_state, quality)
from kitchenrush.v2.ledger import BudgetExceeded, Ledger, SCALE, STAGES, units
from kitchenrush.v2.providers import Client, Model, ProviderError, load_env
from difficulty_sweep_v2 import VariantEngine
from complexity_ladder_v2 import install_wait_contract
from tune_probe_v2 import write_json

NAME = "diverse-complexity-pilot-20260926"
DATA = ROOT / "data/v2" / NAME
RUNS = ROOT / "runs/v2" / NAME
LEDGER = ROOT / "runs/v2/release-r2/spend.sqlite"
PROTOCOL = "kitchen-rush-diverse-pressure-pilot-1"
EXPERT_PREFIXES = ("expert-capacity-pilot-20260924-", "expert-calibration-pilot-20260924-",
                   "complexity-ladder-pilot-20260924-", "balanced-complexity-ladder-pilot-20260925-",
                   NAME + "-")
ROSTER = json.loads((DATA / "roster.json").read_text())
MODELS = {r["model"]["key"]: Model(**r["model"]) for r in ROSTER["configurations"]}
CAPS = {r["model"]["key"]: r["episode_cap_usd"] for r in ROSTER["configurations"]}


def pilot_hash():
    names = ["run_diverse_complexity_pilot_20260926.py", "diverse_complexity_ladder_20260926.py",
             "difficulty_sweep_v2.py", "complexity_ladder_v2.py", "tune_probe_v2.py"]
    paths = [ROOT / "scripts" / n for n in names] + [DATA / "kitchens.json", DATA / "roster.json"]
    return digest({"base_protocol_hash": protocol_hash(), "files": {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}})


def accounted(con):
    own = Ledger._used(con, "WHERE episode LIKE ?", (NAME + "-%",))
    where = "WHERE " + " OR ".join("episode LIKE ?" for _ in EXPERT_PREFIXES)
    combined = Ledger._used(con, where, tuple(p + "%" for p in EXPERT_PREFIXES))
    return own, combined


class CampaignLedger(Ledger):
    def __init__(self, path=LEDGER, *, cap=250.0, opening_balance=16.175129022):
        super().__init__(path, cap=cap, opening_balance=opening_balance)
        self.episode_caps = {}

    def reserve(self, rid, episode, stage, maximum, episode_cap=None):
        amount = units(maximum)
        if stage not in STAGES or episode not in self.episode_caps:
            raise ValueError("missing stage/episode allowance")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM requests WHERE id=?", (rid,)).fetchone():
                raise ValueError("request already reserved; do not redispatch")
            if con.execute("SELECT 1 FROM requests WHERE status IN ('uncertain','overrun')").fetchone():
                raise BudgetExceeded("accounting audit required")
            own, combined = accounted(con)
            gates = [(own, units(ROSTER["campaign_ceiling_usd"])),
                     (combined, units(ROSTER["combined_expert_ceiling_usd"])),
                     (self._used(con) + self.opening_balance, self.cap),
                     (self._used(con, "WHERE stage=?", (stage,)), units(STAGES[stage])),
                     (self._used(con, "WHERE episode=?", (episode,)),
                      units(self.episode_caps[episode]))]
            if any(current + amount > ceiling for current, ceiling in gates):
                raise BudgetExceeded("atomic episode/campaign/Expert/stage/global spending gate")
            con.execute("INSERT INTO requests VALUES (?,?,?,?,NULL,?)",
                        (rid, episode, stage, amount, "reserved"))


def finish(ep, engine, ledger, path, reason, status):
    ep.update(reason=reason, status=status, completed=engine.counters["orders_served"],
              invalid=engine.counters["invalid_actions"], quality=quality(engine),
              decisions=len(ep["actions"]), clock_gs=engine.clock_gs,
              cost_usd=ledger.used(ep["id"]))
    write_json(path, ep)
    return ep


def run_episode(row, model, ledger, *, canary=False):
    eid = f"{NAME}-{model.key}-" + ("canary" if canary else f"level-{row['level']}-{row['seed']}")
    path = RUNS / ("canaries" if canary else "episodes") / f"{eid}.json"
    if path.exists():
        old = json.loads(path.read_text())
        if old["status"] == "running":
            raise ValueError("interrupted episode requires audit; cannot silently restart")
        return old
    if ledger.used(eid):
        raise ValueError("charged episode without artifact; audit required")
    cap = 1.0 if canary else CAPS[model.key]
    ledger.episode_caps[eid] = cap
    engine = VariantEngine(deserialize_spec(row["spec"]), record_trace=True)
    client = Client(model, timeout=600)
    ep = {"id": eid, "protocol": PROTOCOL, "protocol_hash": pilot_hash(),
          "base_protocol_hash": protocol_hash(), "model": asdict(model), "spec": row["spec"],
          "spec_hash": digest(row["spec"]), "level": row["level"], "seed": row["seed"],
          "setting": row["setting"], "feasibility_witness_hash": row["witness_hash"],
          "certificate": row["capacity_certificate"], "dollar_cap": cap, "canary": canary,
          "status": "running", "actions": [], "requests": [], "input_tokens": 0,
          "output_tokens": 0, "accepted_cost_usd": 0, "rank_eligible": False,
          "accounting_contract": "atomic-campaign-reservations-1"}
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, ep)
    while not engine.terminated and len(ep["actions"]) < MAX_DECISIONS:
        remaining = max(0, cap - ledger.used(eid))
        budget = {"kind": "USD", "allowance": cap, "remaining": remaining,
                  "decisions_remaining": MAX_DECISIONS - len(ep["actions"])}
        canonical, options = decision(public_state(engine), budget)
        payload = client.payload(canonical)
        # Serialized bytes conservatively cover this ASCII prompt plus schema/role overhead.
        prompt_bytes = len(json.dumps(payload, ensure_ascii=True).encode()) + 512
        if prompt_bytes > model.prompt_limit:
            return finish(ep, engine, ledger, path, "prompt_ceiling", "incomplete")
        prompt_bound, bound = client.bound(payload)
        rid = f"{eid}:{len(ep['requests'])}"
        try:
            ledger.reserve(rid, eid, "canary" if canary else "pilot", bound)
        except BudgetExceeded as error:
            ep["gate_error"] = str(error)
            return finish(ep, engine, ledger, path, "operational_spending_gate", "incomplete")
        request = {"id": rid, "payload": payload, "reserved_usd": bound, "admitted": False}
        ep["requests"].append(request)
        write_json(path, ep)
        started = time.monotonic()
        try:
            response = client.evaluate(payload)
            request["response"] = response
            n_in, n_out, cost = client.usage(response, payload)
            request.update(input_tokens=n_in, output_tokens=n_out, cost_usd=cost,
                           wall_seconds=time.monotonic() - started)
            ep["input_tokens"] += n_in
            ep["output_tokens"] += n_out
            ledger.settle(rid, cost)
            write_json(path, ep)
            client.validate_identity(response)
            if n_in > prompt_bound or n_out > payload["max_tokens"]:
                raise ProviderError("token reservation bound violated")
            choice = client.select(response)
            call = options.get(choice) if isinstance(choice, str) else None
            request["selection"] = choice
            engine.step([call if call is not None else ToolCall("invalid_model_selection", {})])
            ep["actions"].append(asdict(call if call is not None else ToolCall("invalid_model_selection", {})))
            request["admitted"] = True
            ep["accepted_cost_usd"] += cost
            write_json(path, ep)
            if canary:
                return finish(ep, engine, ledger, path,
                              "canary_ok" if call is not None else "canary_no_selection",
                              "complete" if call is not None else "quarantined")
        except (ProviderError, RuntimeError, ValueError, KeyError) as error:
            # A saved usage settlement remains settled even if a later identity check fails.
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--canaries", action="store_true")
    parser.add_argument("--seeds", type=int, default=1, choices=(1, 2))
    parser.add_argument("--model", choices=MODELS)
    args = parser.parse_args()
    from diverse_complexity_ladder_20260926 import verify_row
    install_wait_contract()
    rows = json.loads((DATA / "kitchens.json").read_text())
    for row in rows:
        verify_row(row)
    ledger = CampaignLedger()
    snap = ledger.snapshot()
    with ledger.connect() as con:
        own, combined = accounted(con)
    print(json.dumps({"protocol_hash": pilot_hash(), "own_usd": own / SCALE,
                      "combined_usd": combined / SCALE, "unresolved": snap["unresolved_requests"],
                      "overruns": snap["overruns"], "planned_episodes": 18 * args.seeds}), flush=True)
    if not args.run:
        return
    if snap["unresolved_requests"] or snap["overruns"]:
        raise ValueError("unresolved ledger")
    load_env(ROOT / ".env")
    keys = [args.model] if args.model else list(MODELS)
    # Every model receives every level on the same seed before adding a second seed.
    if args.canaries:
        first = next(r for r in rows if r["level"] == 0)
        for key in keys:
            ep = run_episode(first, MODELS[key], ledger, canary=True)
            print(json.dumps({"canary": key, "status": ep["status"], "reason": ep["reason"],
                              "error": ep.get("error"), "cost": ep["cost_usd"]}), flush=True)
        return
    usable = []
    for key in keys:
        cp = RUNS / "canaries" / f"{NAME}-{key}-canary.json"
        if cp.exists() and json.loads(cp.read_text())["reason"] == "canary_ok":
            usable.append(key)
        else:
            print(json.dumps({"parked": key, "reason": "no successful canary"}), flush=True)
    for seed in ROSTER["seeds"][:args.seeds]:
        for level in ROSTER["levels"]:
            row = next(r for r in rows if r["level"] == level and r["seed"] == seed)
            for key in usable:
                ep = run_episode(row, MODELS[key], ledger)
                print(json.dumps({"model": key, "level": level, "seed": seed,
                                  "status": ep["status"], "reason": ep["reason"],
                                  "served": ep["completed"], "invalid": ep["invalid"],
                                  "decisions": ep["decisions"], "tokens": ep["output_tokens"],
                                  "billed_usd": ep["cost_usd"]}), flush=True)


if __name__ == "__main__":
    main()

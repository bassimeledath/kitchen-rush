#!/usr/bin/env python3
"""Replay continuation actions, reconcile charges, then merge scored assignments."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict

import run_new_frontier_four_20260929 as prior
import run_new_frontier_four_recovery_20260930 as rec
from kitchenrush.tools import ToolCall
from kitchenrush.v2.ledger import units


def summarize(episodes):
    by_cell = defaultdict(list)
    for ep in episodes:
        by_cell[ep["level"], ep["key"]].append(ep)
    result = {}
    for level in prior.LEVELS:
        for key in prior.MODELS:
            cell = by_cell[level, key]
            scored = [e for e in cell if e["scored"]]
            finished = sum(e["orders"] for e in scored if e["clear"])
            requested = sum(e["orders"] for e in scored)
            decisions = sum(e["decisions"] for e in scored)
            output = sum(e["generation_tokens"] for e in scored)
            frozen = sum(e["frozen_usd"] for e in scored)
            served = sum(e["served"] for e in scored)
            invalid = sum(e["invalid"] for e in scored)
            result[f"{level}|{key}"] = {
                "planned": 12, "attempted": len(cell), "scored": len(scored),
                "unscored": sum(e["status"] not in ("running", "complete") for e in cell),
                "running": sum(e["status"] == "running" for e in cell),
                "clear_kitchens": sum(e["clear"] for e in scored),
                "served_orders": served, "requested_orders": requested,
                "finished_orders": finished, "generation_tokens": output,
                "frozen_usd": frozen, "billed_usd": sum(e["billed_usd"] for e in scored),
                "invalid": invalid, "decisions": decisions,
                "tokens_per_finished_order": output / finished if finished else None,
                "usd_per_finished_order": frozen / finished if finished else None,
                "order_completion_pct": 100 * served / requested if requested else None,
                "invalid_action_pct": 100 * invalid / decisions if decisions else None,
            }
    return result


def main():
    protocol_hash = rec.check_manifest()
    rec.validate_assignments()
    original = json.loads((prior.RUNS / "audit.json").read_text())
    ledger = prior.CampaignLedger(rec.RUNS / "spend.sqlite",
                                  cap=rec.CAMPAIGN["inference_ceiling_usd"])
    with ledger.connect() as con:
        records = {r[0]: r for r in con.execute(
            "SELECT id,episode,stage,reserved,actual,status FROM requests").fetchall()}
    seen = set()
    verified = []
    for path in sorted((rec.RUNS / "episodes").glob("*.json")):
        ep = json.loads(path.read_text())
        key, level, seed = ep["model"]["key"], ep["level"], ep["seed"]
        a = next(a for a in rec.ASSIGNMENTS if (a["level"], a["seed"], a["key"]) == (level, seed, key))
        row = next(r for r in prior.rows(level) if r["seed"] == seed)
        assert ep["id"] == path.stem == f"{rec.NAME}-{key}-{level}-{seed}"
        assert ep["protocol"] == prior.PROTOCOL
        assert ep["continuation_protocol"] == "fixed-all-missing-assignment-continuation-1"
        assert ep["protocol_hash"] == protocol_hash
        assert ep["original_protocol_hash"] == prior.check_manifest()
        assert ep["original_status"] == a["original_status"]
        assert ep["original_sha256"] == a["original_sha256"]
        assert ep["model"] == asdict(prior.MODELS[key])
        assert ep["spec"] == row["spec"] and ep["spec_hash"] == prior.base.digest(row["spec"])
        assert ep["feasibility_witness_hash"] == row["witness_hash"]
        assert ep["dollar_cap"] == rec.CAMPAIGN["episode_ceiling_usd"]
        prior.env.WAITS = prior.HARD_WAITS if level == "hard" else prior.NORMAL_WAITS
        engine = prior.engine_for(level, row)
        client = prior.transport.BoundedStreamingClient(prior.MODELS[key])
        actions = []
        n_in = n_out = spent = accepted = 0
        for i, req in enumerate(ep["requests"]):
            rid = f"{ep['id']}:{i}"
            assert req["id"] == rid and rid not in seen
            seen.add(rid)
            record = records[rid]
            assert record[1] == ep["id"] and record[2] == "main"
            budget = {"kind": "USD", "allowance": ep["dollar_cap"],
                      "remaining": max(0, ep["dollar_cap"] - spent / 1e9),
                      "decisions_remaining": rec.CAMPAIGN["maximum_decisions_per_episode"] - len(actions)}
            canonical, options = prior.base.decision(prior.base.public_state(engine), budget)
            assert req["payload"] == client.payload(canonical)
            prompt_bound, bound = client.bound(req["payload"])
            assert units(bound) == record[3] == units(req["reserved_usd"])
            if "response" in req:
                response = req["response"]
                assert prior.transport.prior.aggregate_stream(response["stream_chunks"]) == {
                    k: v for k, v in response.items() if k != "stream_chunks"}
                ni, no, cost = client.usage(response, req["payload"])
                assert (ni, no) == (req["input_tokens"], req["output_tokens"])
                assert units(cost) == record[4] == units(req["cost_usd"])
                assert record[5] == "settled"
                assert ni <= prompt_bound and no <= req["payload"]["max_tokens"]
                n_in += ni
                n_out += no
                if req["admitted"]:
                    client.validate_identity(response)
                    choice = client.select(response)
                    assert choice == req["selection"]
                    action = options.get(choice) if isinstance(choice, str) else None
                    if action is None:
                        action = ToolCall("invalid_model_selection", {})
                    engine.step([action])
                    actions.append(asdict(action))
                    accepted += units(req["cost_usd"])
            else:
                assert not req["admitted"] and record[5] in ("reserved", "retained_failure")
            spent += record[4] if record[4] is not None else record[3]
        assert actions == ep["actions"] and (n_in, n_out) == (ep["input_tokens"], ep["output_tokens"])
        assert abs(ep["accepted_cost_usd"] - accepted / 1e9) < 1e-7
        if ep["status"] != "running":
            assert ep["completed"] == engine.counters["orders_served"]
            assert ep["invalid"] == engine.counters["invalid_actions"]
            assert ep["quality"] == prior.base.quality(engine)
            assert ep["decisions"] == len(actions)
            assert abs(ep["clock_gs"] - engine.clock_gs) < 1e-8
            assert abs(ep["cost_usd"] - spent / 1e9) < 1e-7
        scored = ep["status"] == "complete" and ep.get("reason") in ("game_terminal", "decision_limit")
        assert ep["rank_eligible"] == scored if ep["status"] != "running" else not ep["rank_eligible"]
        if scored:
            assert engine.terminated if ep["reason"] == "game_terminal" else len(actions) == rec.CAMPAIGN["maximum_decisions_per_episode"]
        verified.append({"level": level, "seed": seed, "key": key, "status": ep["status"],
                         "reason": ep.get("reason"), "scored": scored,
                         "served": ep.get("completed"), "orders": len(row["spec"]["orders"]),
                         "clear": int(ep.get("completed", -1) == len(row["spec"]["orders"])) if scored else None,
                         "input_tokens": n_in, "generation_tokens": n_out,
                         "frozen_usd": n_in * prior.MODELS[key].input_price + n_out * prior.MODELS[key].output_price,
                         "billed_usd": spent / 1e9, "invalid": ep.get("invalid"),
                         "decisions": len(actions), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "cohort": "recovery"})
    assert seen == set(records), (len(seen), len(records))
    combined = [dict(ep, cohort="original") for ep in original["episodes"] if ep["scored"]]
    combined += [ep for ep in verified if ep["scored"]]
    ids = [(e["level"], e["seed"], e["key"]) for e in combined]
    assert len(ids) == len(set(ids))
    result = {"original_protocol_hash": prior.check_manifest(),
              "continuation_protocol_hash": protocol_hash,
              "original_ledger": original["ledger"], "continuation_ledger": ledger.snapshot(),
              "combined_accounted_usd": original["ledger"]["accounted_usd"] + ledger.snapshot()["accounted_usd"],
              "summary": summarize(combined), "episodes": combined,
              "continuation_attempts": verified,
              "original_attempts": len(original["episodes"]),
              "original_scored": sum(e["scored"] for e in original["episodes"])}
    assert result["combined_accounted_usd"] <= rec.CAMPAIGN["combined_with_original_accounted_ceiling_usd"]
    prior.save(rec.RUNS / "audit.json", result)
    print(json.dumps({"combined_scored": len(combined),
                      "continuation_attempts": len(verified),
                      "combined_accounted_usd": result["combined_accounted_usd"]}, indent=2))


if __name__ == "__main__":
    main()

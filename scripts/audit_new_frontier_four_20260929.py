#!/usr/bin/env python3
"""Replay the frozen four-configuration campaign and audit its accounting."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict

import run_new_frontier_four_20260929 as run
from kitchenrush.tools import ToolCall
from kitchenrush.v2.ledger import units


def main():
    manifest = json.loads((run.RUNS / "source-manifest.json").read_text())
    assert manifest == {"protocol": run.PROTOCOL, "protocol_hash": run.frozen_hash(),
                        "files": run.source_files()}
    rows = {(level, row["seed"]): row for level in run.LEVELS for row in run.rows(level)}
    ledger = run.CampaignLedger(run.RUNS / "spend.sqlite", cap=run.CAMPAIGN["inference_ceiling_usd"])
    with ledger.connect() as con:
        records = {r[0]: r for r in con.execute(
            "SELECT id,episode,stage,reserved,actual,status FROM requests").fetchall()}
    seen = set()
    episodes = []
    by_cell = defaultdict(list)
    for path in sorted((run.RUNS / "episodes").glob("*.json")):
        ep = json.loads(path.read_text())
        key, level, seed = ep["model"]["key"], ep["level"], ep["seed"]
        row = rows[level, seed]
        assert ep["id"] == path.stem == f"{run.NAME}-{key}-{level}-{seed}"
        assert ep["protocol"] == run.PROTOCOL and ep["protocol_hash"] == manifest["protocol_hash"]
        assert ep["model"] == asdict(run.MODELS[key])
        assert ep["spec"] == row["spec"] and ep["spec_hash"] == run.base.digest(row["spec"])
        assert ep["feasibility_witness_hash"] == row["witness_hash"]
        assert ep["dollar_cap"] == run.EPISODE_CAP
        run.env.WAITS = run.HARD_WAITS if level == "hard" else run.NORMAL_WAITS
        engine = run.engine_for(level, row)
        client = run.transport.BoundedStreamingClient(run.MODELS[key])
        spent = accepted = n_in = n_out = 0
        actions = []
        for i, req in enumerate(ep["requests"]):
            rid = f"{ep['id']}:{i}"
            assert req["id"] == rid and rid not in seen
            seen.add(rid)
            record = records[rid]
            assert record[1] == ep["id"] and record[2] == "main"
            budget = {"kind": "USD", "allowance": ep["dollar_cap"],
                      "remaining": max(0, ep["dollar_cap"] - spent / 1e9),
                      "decisions_remaining": run.CAMPAIGN["maximum_decisions_per_episode"] - len(actions)}
            canonical, options = run.base.decision(run.base.public_state(engine), budget)
            assert req["payload"] == client.payload(canonical)
            prompt_bound, bound = client.bound(req["payload"])
            assert units(bound) == record[3] == units(req["reserved_usd"])
            if "response" in req:
                response = req["response"]
                assert run.transport.prior.aggregate_stream(response["stream_chunks"]) == {
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
        assert actions == ep["actions"]
        assert (n_in, n_out) == (ep["input_tokens"], ep["output_tokens"])
        assert abs(ep["accepted_cost_usd"] - accepted / 1e9) < 1e-7
        if ep["status"] != "running":
            assert ep["completed"] == engine.counters["orders_served"]
            assert ep["invalid"] == engine.counters["invalid_actions"]
            assert ep["quality"] == run.base.quality(engine)
            assert ep["decisions"] == len(actions)
            assert abs(ep["clock_gs"] - engine.clock_gs) < 1e-8
            assert abs(ep["cost_usd"] - spent / 1e9) < 1e-7
        scored = ep["status"] == "complete" and ep.get("reason") in ("game_terminal", "decision_limit")
        assert ep["rank_eligible"] == scored if ep["status"] != "running" else not ep["rank_eligible"]
        if scored:
            assert engine.terminated if ep["reason"] == "game_terminal" else len(actions) == run.CAMPAIGN["maximum_decisions_per_episode"]
        item = {"level": level, "seed": seed, "key": key, "status": ep["status"],
                "reason": ep.get("reason"), "scored": scored,
                "served": ep.get("completed"), "orders": len(row["spec"]["orders"]),
                "clear": int(ep.get("completed", -1) == len(row["spec"]["orders"])) if scored else None,
                "input_tokens": n_in, "generation_tokens": n_out,
                "frozen_usd": n_in * run.MODELS[key].input_price + n_out * run.MODELS[key].output_price,
                "billed_usd": spent / 1e9, "invalid": ep.get("invalid"), "decisions": len(actions),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        episodes.append(item)
        by_cell[level, key].append(item)
    for path in sorted((run.RUNS / "canaries").glob("*.json")):
        canary = json.loads(path.read_text())
        assert canary["id"] == path.stem
        assert canary["protocol_hash"] == manifest["protocol_hash"]
        assert canary["model"] == asdict(run.MODELS[canary["model"]["key"]])
        assert canary["status"] == "complete" and len(canary["requests"]) == 1
        req = canary["requests"][0]
        rid = canary["id"] + ":0"
        assert req["id"] == rid and rid not in seen
        seen.add(rid)
        record = records[rid]
        assert record[1] == canary["id"] and record[2] == "canary" and record[5] == "settled"
        assert units(req["reserved_usd"]) == record[3]
        assert units(req["cost_usd"]) == record[4]
        assert units(canary["cost_usd"]) == record[4]
    assert seen == set(records), (len(seen), len(records))
    summary = {}
    for level in run.LEVELS:
        for key in run.MODELS:
            cell = by_cell[level, key]
            scored = [ep for ep in cell if ep["scored"]]
            finished_orders = sum(ep["orders"] for ep in scored if ep["clear"])
            requested = sum(ep["orders"] for ep in scored)
            decisions = sum(ep["decisions"] for ep in scored)
            summary[f"{level}|{key}"] = {
                "planned": len(run.rows(level)), "attempted": len(cell), "scored": len(scored),
                "unscored": sum(ep["status"] not in ("running", "complete") for ep in cell),
                "running": sum(ep["status"] == "running" for ep in cell),
                "clear_kitchens": sum(ep["clear"] for ep in scored),
                "served_orders": sum(ep["served"] for ep in scored),
                "requested_orders": requested, "finished_orders": finished_orders,
                "generation_tokens": sum(ep["generation_tokens"] for ep in scored),
                "frozen_usd": sum(ep["frozen_usd"] for ep in scored),
                "billed_usd": sum(ep["billed_usd"] for ep in scored),
                "invalid": sum(ep["invalid"] for ep in scored), "decisions": decisions,
                "tokens_per_finished_order": (sum(ep["generation_tokens"] for ep in scored) / finished_orders
                                              if finished_orders else None),
                "usd_per_finished_order": (sum(ep["frozen_usd"] for ep in scored) / finished_orders
                                           if finished_orders else None),
                "order_completion_pct": (100 * sum(ep["served"] for ep in scored) / requested
                                         if requested else None),
                "invalid_action_pct": (100 * sum(ep["invalid"] for ep in scored) / decisions
                                       if decisions else None),
            }
    result = {"protocol_hash": manifest["protocol_hash"], "ledger": ledger.snapshot(),
              "summary": summary, "episodes": episodes}
    run.save(run.RUNS / "audit.json", result)
    print(json.dumps({"episodes": len(episodes), "ledger": result["ledger"],
                      "summary": summary}, indent=2))


if __name__ == "__main__":
    main()

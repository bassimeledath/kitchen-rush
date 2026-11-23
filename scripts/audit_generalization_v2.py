#!/usr/bin/env python3
"""Offline, adversarial profile-generalization audit. Never calls model APIs.

Run: python scripts/audit_generalization_v2.py
Outputs 1,200 sampled kitchens, 3,600 baseline replays and 120 paired controls.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import statistics
import sys
import time
from dataclasses import asdict, replace
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kitchenrush import config  # noqa: E402
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2 import environment as env  # noqa: E402

BASELINES = ("public_rule_reader", "fixed_table", "fixed_preparation_live_timing")


def compile_table():
    """Privileged lookup includes EVERY card, even evaluation split cards."""
    table = {}
    for split in env.SPLITS:
        for size in (2, 3, 4):
            for card in env.recipe_pool(size, split):
                template = []
                for ingredient, terminal in sorted(card.items(), key=lambda kv: kv[1] == "COOKED"):
                    rule = config.INGREDIENTS[ingredient]
                    template.append(("collect", {"ingredient": ingredient}))
                    if terminal == "CHOPPED" or (
                        terminal == "COOKED" and rule.cookable_from == "CHOPPED"
                    ):
                        template.append(("chop", {"ingredient": ingredient}))
                    if terminal == "COOKED":
                        template.append(("cook", {"ingredient": ingredient}))
                        delay = rule.cook_time
                        while delay:
                            seconds = max(n for n in env.WAITS if n <= delay)
                            template.append(("wait", {"seconds": seconds}))
                            delay -= seconds
                        template.append(("collect_cooked", {"ingredient": ingredient}))
                table[env.recipe_signature(card)] = template
    return table


class FixedTable:
    def __init__(self, table):
        self.table = table
        self.queue = []

    def choose(self, state):
        if not self.queue:
            orders = [o for o in state["orders"] if o["status"] == "ACTIVE"]
            if not orders:
                return ToolCall("wait", {"seconds": 1})
            order = min(orders, key=lambda o: o["deadline_gs"])
            recipe = order["dish"]
            self.queue = copy.deepcopy(
                self.table[env.recipe_signature(state["recipe_cards"][recipe])]
            ) + [("plate", {"recipe": recipe}), ("serve", {"order_id": order["order_id"]})]
        name, args = self.queue.pop(0)
        if name == "collect_cooked":
            args["burner_index"] = next(
                (b["burner_index"] for b in state["burners"]
                 if b["ingredient"] == args["ingredient"]), 0
            )
        return ToolCall(name, args)


def run(spec, kind, table):
    engine = env.Engine(spec, record_trace=True)
    agent = FixedTable(table) if kind == "fixed_table" else env.RecipeReader()
    actions = []
    while not engine.terminated and len(actions) < env.MAX_DECISIONS:
        state = env.public_state(engine)
        if kind == "fixed_preparation_live_timing":
            state["ingredient_rules"] = {i: asdict(p) for i, p in config.INGREDIENTS.items()}
        action = agent.choose(state)
        actions.append(asdict(action))
        engine.step([action])
    episode = {
        "spec": env.serialize_spec(spec), "actions": actions,
        "frames": engine.trace, "quality": env.quality(engine),
    }
    env.verify_replay(episode)
    return {
        "q": episode["quality"], "served": engine.counters["orders_served"],
        "invalid": engine.counters["invalid_actions"], "burns": engine.counters["burns"],
        "actions": len(actions),
    }


def summarize(rows, baselines):
    result = {}
    for baseline in baselines:
        values = [r[baseline] for r in rows]
        result[baseline] = {
            "mean_quality": statistics.mean(v["q"] for v in values),
            "q100": sum(v["q"] == 100 for v in values),
            "zero_invalid": sum(v["invalid"] == 0 for v in values),
            "mean_served": statistics.mean(v["served"] for v in values),
            "mean_invalid": statistics.mean(v["invalid"] for v in values),
            "mean_burns": statistics.mean(v["burns"] for v in values),
            "max_actions": max(v["actions"] for v in values),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--control-seeds", type=int, default=10)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data/v2/release/generalization-audit.json")
    args = parser.parse_args()
    if args.seeds <= 0 or args.control_seeds <= 0:
        parser.error("seed counts must be positive")
    started = time.monotonic()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    source_hash = env.protocol_hash()
    original = copy.deepcopy(config.INGREDIENTS)
    # Cache the pure enumerator for speed; no catalog, source or returned cards are mutated.
    env.recipe_pool = lru_cache(None)(env.recipe_pool)
    table = compile_table()
    rows, controls = [], []
    signatures = {s: set() for s in env.SPLITS}
    useful = {s: set() for s in env.SPLITS}
    for split in env.SPLITS:
        for size in (2, 3, 4):
            for seed in range(61000, 61000 + args.seeds):
                spec = env.generate(seed, split, size)
                spec_hash = env.digest(env.serialize_spec(spec))
                assert spec_hash == env.digest(env.serialize_spec(env.generate(seed, split, size)))
                signature = env.digest(spec.ingredient_rules)
                signatures[split].add(signature)
                cooked = sorted({i for c in spec.recipe_cards.values()
                                 for i, state in c.items() if state == "COOKED"})
                relevant = env.digest({i: spec.ingredient_rules[i] for i in cooked})
                useful[split].add(relevant)
                row = {
                    "split": split, "size": size, "seed": seed, "spec_hash": spec_hash,
                    "profile_hash": signature, "task_relevant_profile_hash": relevant,
                    "cooked_ingredients": cooked,
                    "raw_mushroom_required": "mushroom" in cooked
                    and spec.ingredient_rules["mushroom"]["cookable_from"] == "RAW",
                }
                for baseline in BASELINES:
                    row[baseline] = run(spec, baseline, table)
                rows.append(row)
            for seed in range(62000, 62000 + args.control_seeds):
                spec = env.generate(seed, split, size)
                spec = replace(spec, orders=[replace(o, deadline_gs=500) for o in spec.orders])
                canonical = replace(spec, ingredient_rules={
                    i: asdict(p) for i, p in config.INGREDIENTS.items()
                })
                controls.append({
                    "seed": seed, "split": split, "size": size,
                    "canonical": run(canonical, "fixed_table", table),
                    "variable": run(spec, "fixed_table", table),
                })
    overlaps = [{
        "splits": [a, b], "full_profile_overlap": len(signatures[a] & signatures[b]),
        "task_relevant_profile_overlap": len(useful[a] & useful[b]),
    } for a in env.SPLITS for b in env.SPLITS if a < b]
    failures = [r for r in rows if r["public_rule_reader"]["q"] != 100
                or r["public_rule_reader"]["invalid"]]
    source_end = env.protocol_hash()
    if source_end != source_hash:
        raise RuntimeError("core source changed during audit; do not publish mixed evidence")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != script_hash:
        raise RuntimeError("audit script changed during audit; do not publish mixed evidence")
    result = {
        "audit": "independent sampled generalization stress and privileged memorization baselines",
        "script_sha256": script_hash,
        "reproduce": "python scripts/audit_generalization_v2.py",
        "source_hash_start": source_hash, "source_hash_end": source_end, "source_stable": True,
        "seeds": {"start": 61000, "count": args.seeds,
                  "splits": list(env.SPLITS), "sizes": [2, 3, 4]},
        "episodes": len(rows), "replayed_baseline_runs": len(rows) * len(BASELINES),
        "table_cards": len(table), "summary": summarize(rows, BASELINES),
        "methods": {
            "fixed_table": "All recipe cards, including held-out cards, compiled to fixed "
            "preparation/wait scripts using legacy catalog. Binds public order/card IDs, EDF "
            "between scripts, public burner index only; no legality filter or recovery.",
            "fixed_preparation_live_timing": "RecipeReader sees correct cards and live ready "
            "timestamps but legacy ingredient rules. Isolates preparation-rule failures.",
            "public_rule_reader": "RecipeReader consumes actual public cards/profiles/timestamps. "
            "All baselines use the real engine, same decision cap and replay verification.",
        },
        "profile_diversity": {s: {"whole_unique": len(signatures[s]),
                                  "task_relevant_unique": len(useful[s])} for s in env.SPLITS},
        "cross_split_overlap": overlaps,
        "no_cooked_ingredient_episodes": sum(not r["cooked_ingredients"] for r in rows),
        "raw_mushroom_cooked_episodes": sum(r["raw_mushroom_required"] for r in rows),
        "global_catalog_unchanged": config.INGREDIENTS == original,
        "solver_failures": failures, "rows": rows,
        "fixed_table_positive_control": {
            "description": "Paired kitchens with identical cards/arrivals/layout and generous "
            "500-second deadlines. Only profiles differ: legacy vs generated. Validates fixed "
            "lookup implementation and isolates profiles from deadline feasibility. Both replayed.",
            "pairs": len(controls), "summary": summarize(controls, ("canonical", "variable")),
            "rows": controls,
        },
        "seconds": time.monotonic() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "episodes": len(rows), "solver_failures": len(failures), "summary": result["summary"],
        "positive_control": result["fixed_table_positive_control"]["summary"],
    }, indent=2))
    if (failures or not result["global_catalog_unchanged"]
            or any(p["full_profile_overlap"] for p in overlaps)
            or not any(r["variable"]["q"] < r["canonical"]["q"] for r in controls)
            or any(r["canonical"]["q"] != 100 or r["canonical"]["invalid"] for r in controls)):
        raise SystemExit("audit failed; evidence preserved")


if __name__ == "__main__":
    main()

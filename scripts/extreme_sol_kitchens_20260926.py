#!/usr/bin/env python3
"""Feasible extreme kitchens: repeated six-burner waves, four-slot exact inventory."""
from __future__ import annotations
import argparse
import copy
import itertools
import json
import math
import random
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from kitchenrush import config
from kitchenrush.procgen import OrderSpec
from kitchenrush.tools import ToolCall
from kitchenrush.v2 import environment as env
from complexity_ladder_v2 import make_spec as base_spec
from difficulty_sweep_v2 import VariantEngine
from concurrency_probe_v2 import serve_times

NAME = "extreme-sol-reasoning-20260926"
DATA = ROOT / "data/v2" / NAME
SEEDS = (90601, 90602)
ARMS = {"brutal": (10, 2), "severe": (18, 8)}
WAITS = (1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150)

def install_wait_contract():
    env.WAITS = WAITS

def make_spec(seed, arm):
    base = base_spec(6, seed)
    rng = random.Random(f"extreme-sol:{seed}")
    cookables = ("broth_base", "egg", "mushroom", "noodles", "patty")
    cards = [dict([*((i, "COOKED") for i in group), (side, "CHOPPED")])
             for group in itertools.combinations(cookables, 3)
             for side in ("lettuce", "onion", "tomato")]
    cards = {f"R{i}": c for i, c in enumerate(rng.sample(cards, 6), 1)}
    cells = [s.cell for s in base.stations]
    rng.shuffle(cells)
    stations = [replace(s, cell=cell) for s, cell in zip(base.stations, cells)]
    times = [50, 75, 100, 125, 150]
    rng.shuffle(times)
    profiles = copy.deepcopy(base.ingredient_rules)
    for ingredient, duration in zip(cookables, times):
        profiles[ingredient].update(cook_time=duration, burn_window=ARMS[arm][0])
    profiles["mushroom"]["cookable_from"] = "CHOPPED"
    return replace(base, tier=f"experimental-extreme-sol-{arm}",
                   recipe_cards=cards, active_recipes=tuple(cards), stations=stations,
                   ingredient_rules=profiles,
                   orders=[OrderSpec(f"O{i}", f"R{i}", 0, 4000, 1) for i in range(1, 7)],
                   horizon_gs=5000)

def witness(spec):
    install_wait_contract()
    e = VariantEngine(spec, record_trace=True)
    actions = []
    def act(name, **arguments):
        if e.terminated:
            raise ValueError("witness terminated early")
        call = ToolCall(name, arguments)
        e.step([call])
        actions.append(asdict(call))
        if e.counters["invalid_actions"] or e.counters["burns"]:
            raise ValueError(f"bad witness: {name} {arguments} at {e.clock_gs}")
    def wait_until(target):
        remaining = max(0, math.ceil(target-e.clock_gs-1e-8))
        # Minimize explicit wait decisions; the model's 160-action budget stays ample.
        best = [0] + [10_000]*remaining
        picks = [0]*(remaining+1)
        for n in range(1, remaining+1):
            for w in WAITS:
                if w <= n and best[n-w]+1 < best[n]:
                    best[n], picks[n] = best[n-w]+1, w
        while remaining:
            w = picks[remaining]
            act("wait", seconds=w)
            remaining -= w
    for start in range(0, 6, 2):
        orders = spec.orders[start:start+2]
        target = e.clock_gs + max(p["cook_time"] for p in spec.ingredient_rules.values()) + 40
        jobs = [(o, ing, target + 32*j) for j, o in enumerate(orders)
                for ing, state in spec.recipe_cards[o.dish].items() if state == "COOKED"]
        jobs.sort(key=lambda job: job[2] - spec.ingredient_rules[job[1]]["cook_time"])
        indices = {}
        for order, ingredient, ready_target in jobs:
            act("collect", ingredient=ingredient)
            if e.ingredient_rules[ingredient].cookable_from == "CHOPPED":
                act("chop", ingredient=ingredient)
            # Predict the walking/handling duration using the same public engine rules.
            probe = copy.deepcopy(e)
            before = {i for i, burner in enumerate(probe.burners) if burner.job}
            probe.step([ToolCall("cook", {"ingredient": ingredient})])
            added = {i for i, burner in enumerate(probe.burners) if burner.job} - before
            if len(added) != 1 or probe.counters["invalid_actions"]:
                raise ValueError("cook prediction failed")
            predicted_index = added.pop()
            delta = probe.burners[predicted_index].job.ready_gs - e.clock_gs
            wait_until(ready_target - delta)
            before = {i for i, burner in enumerate(e.burners) if burner.job}
            act("cook", ingredient=ingredient)
            index = ({i for i, burner in enumerate(e.burners) if burner.job} - before).pop()
            indices[order.order_id, ingredient] = index
        for order in orders:
            card = spec.recipe_cards[order.dish]
            side = next(i for i, state in card.items() if state == "CHOPPED")
            act("collect", ingredient=side)
            act("chop", ingredient=side)
            cooked = [i for i, state in card.items() if state == "COOKED"]
            cooked.sort(key=lambda i: e.burners[indices[order.order_id, i]].job.ready_gs)
            for ingredient in cooked:
                index = indices[order.order_id, ingredient]
                wait_until(e.burners[index].job.ready_gs)
                act("collect_cooked", ingredient=ingredient, burner_index=index)
            act("plate", recipe=order.dish)
            act("serve", order_id=order.order_id)
    return actions, e

def verify(row):
    install_wait_contract()
    assert env.digest(row["witness_actions"]) == row["witness_hash"]
    e = VariantEngine(env.deserialize_spec(row["spec"]), record_trace=True)
    for action in row["witness_actions"]:
        assert not e.terminated
        e.step([ToolCall(**action)])
    assert e.counters["orders_served"] == 6
    assert not e.counters["invalid_actions"] and not e.counters["burns"]
    assert len(row["witness_actions"]) < env.MAX_DECISIONS
    assert max(len(f["hands"]) for f in e.trace) == 4
    assert max(sum(b["status"] != "FREE" for b in f["burners"]) for f in e.trace) == 6
    assert certificate(env.deserialize_spec(row["spec"])) == row["capacity_certificate"]
    return e

def certificate(spec):
    end = max(o.deadline_gs for o in spec.orders)
    mandatory = [sum(spec.ingredient_rules[i]["cook_time"]
                     for i,s in spec.recipe_cards[o.dish].items() if s == "COOKED")
                 for o in spec.orders]
    active = [max(spec.ingredient_rules[i]["cook_time"]
                  for i,s in spec.recipe_cards[o.dish].items() if s == "COOKED")
              for o in spec.orders]
    return {"kind": "mandatory-cook-occupancy-and-active-order-duration-bound",
            "latest_deadline_gs": end, "mandatory_cook_work_gs": sum(mandatory),
            "lower_bound_burners": math.ceil(sum(mandatory)/end),
            "sum_active_order_duration_lb_gs": sum(active),
            "lower_bound_active_orders": math.ceil(sum(active)/end),
            "preconditions": ["all orders arrive at zero", "empty initial inventory",
                              "each component unit is consumed once", "all cooked units needed before delivery"]}

def build(seed, arm):
    spec = make_spec(seed, arm)
    actions, e = witness(spec)
    times = serve_times(e)
    margin = ARMS[arm][1]
    spec = replace(spec, orders=[replace(o, deadline_gs=math.ceil(times[o.order_id]+margin))
                                for o in spec.orders],
                   horizon_gs=math.ceil(max(times.values())+margin+30))
    row = {"arm": arm, "level": 3 if arm == "brutal" else 4,
           "seed": seed, "split": "adaptive-development-calibration",
           "spec": env.serialize_spec(spec), "witness_actions": actions,
           "witness_hash": env.digest(actions), "witness_decisions": len(actions),
           "witness_serve_times": times, "capacity_certificate": certificate(spec), "setting": {
               "orders": 6, "burners": 6, "hand_slots": 4,
               "cooked_components_per_order": 3, "chopped_sides_per_order": 1,
               "burn_window_gs": ARMS[arm][0], "deadline_witness_margin_gs": margin,
               "cooking_waves": 3, "cook_times_gs": [50,75,100,125,150],
               "wait_choices_gs": list(WAITS),
               "max_decisions": env.MAX_DECISIONS}}
    verify(row)
    return row

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    path = DATA / "kitchens.json"
    if args.verify:
        rows = json.loads(path.read_text())
        for row in rows: verify(row)
    else:
        if path.exists(): raise ValueError("dataset already frozen")
        rows = [build(seed, arm) for arm in ARMS for seed in SEEDS]
        DATA.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2, sort_keys=True)+"\n")
    print(json.dumps([{"arm": r["arm"], "seed": r["seed"],
                       "witness_decisions": r["witness_decisions"],
                       "deadlines": [o["deadline_gs"] for o in r["spec"]["orders"]]} for r in rows]))

if __name__ == "__main__": main()

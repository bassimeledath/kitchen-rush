#!/usr/bin/env python3
"""Freeze a diverse four-order ladder varying only deadline pressure per seed.

L0 has a replayed serial schedule. L1 and L2 have sound lower bounds of at
least two and three simultaneously active orders respectively. Bounds are
not exact optimal schedules. Extended timers use the exploratory engine.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from kitchenrush import config
from kitchenrush.tools import ToolCall
from kitchenrush.v2 import environment as env
from complexity_ladder_v2 import make_spec as old_base, install_wait_contract, WAITS
from concurrency_probe_v2 import serve_times
from difficulty_sweep_v2 import VariantEngine

NAME = "diverse-complexity-pilot-20260926"
OUT = ROOT / "data/v2" / NAME / "kitchens.json"
LEVELS = (0, 1, 2)
SEEDS = (90501, 90502)
BATCH_SIZE = {0: 1, 1: 2, 2: 4}
MARGIN_GS = {0: 120, 1: 60, 2: 60}
REQUIRED_ORDER_LB = {0: 1, 1: 2, 2: 3}


def shared_spec(seed):
    """Same recipe/profile/layout task at every level; no inherited split claim."""
    if seed not in SEEDS:
        raise ValueError("seed not declared")
    base = old_base(4, seed)
    rng = random.Random(f"diverse-complexity:{seed}")
    cards = [
        {"mushroom": "COOKED", "tomato": "CHOPPED"},
        {"broth_base": "COOKED", "bun": "RAW", "lettuce": "CHOPPED"},
        {"egg": "COOKED", "cheese": "RAW", "onion": "CHOPPED", "tomato": "RAW"},
        {"patty": "COOKED", "bun": "RAW", "lettuce": "RAW"},
    ]
    # Recipe order/identifier does not reliably identify preparation pattern.
    rng.shuffle(cards)
    cards = {f"R{i}": card for i, card in enumerate(cards, 1)}
    cells = [s.cell for s in base.stations]
    rng.shuffle(cells)
    stations = [replace(s, cell=c) for s, c in zip(base.stations, cells)]
    durations = [360, 400, 440, 480]
    rng.shuffle(durations)
    times = dict(zip(("mushroom", "broth_base", "egg", "patty"), durations))
    profiles = {i: dict(p) for i, p in base.ingredient_rules.items()}
    for ingredient, profile in profiles.items():
        if profile["cookable_from"]:
            profile.update(cook_time=times.get(ingredient, 400), burn_window=180)
    profiles["mushroom"]["cookable_from"] = "CHOPPED"
    return replace(base, tier="v2-diverse-pressure-development", stations=stations,
                   active_recipes=tuple(cards), recipe_cards=cards,
                   ingredient_rules=profiles,
                   orders=[replace(o, deadline_gs=4000) for o in base.orders[:4]],
                   horizon_gs=5000)


def witness(spec, batch_size):
    """Read public rules; align ready times within each wave; exact order inventory."""
    install_wait_contract()
    e = VariantEngine(spec, record_trace=True)
    actions = []
    active = set()
    peak_orders = 0
    starts = {}
    def act(name, **arguments):
        if e.terminated:
            raise ValueError("witness already terminated")
        call = ToolCall(name, arguments)
        e.step([call])
        actions.append(asdict(call))
        if e.counters["invalid_actions"] or e.counters["burns"]:
            raise ValueError(f"witness action {name} {arguments} failed at {e.clock_gs}")
    def wait_until(target):
        while target > e.clock_gs:
            left = target - e.clock_gs
            act("wait", seconds=max(v for v in WAITS if v <= max(1, left)))
    for first in range(0, 4, batch_size):
        batch = spec.orders[first:first+batch_size]
        jobs = [(o, ingredient) for o in batch
                for ingredient, state in spec.recipe_cards[o.dish].items() if state == "COOKED"]
        if any(sum(state == "COOKED" for state in spec.recipe_cards[o.dish].values()) != 1 for o in batch):
            raise ValueError("ladder certificate requires one cooked unit per order")
        jobs.sort(key=lambda pair: spec.ingredient_rules[pair[1]]["cook_time"], reverse=True)
        target = e.clock_gs + max(spec.ingredient_rules[i]["cook_time"] for _, i in jobs) + 10
        indices = {}
        for o, ingredient in jobs:
            # Each cooked ingredient is unique to its order, so no unit-assignment ambiguity.
            starts[o.order_id] = e.clock_gs
            active.add(o.order_id)
            peak_orders = max(peak_orders, len(active))
            act("collect", ingredient=ingredient)
            profile = spec.ingredient_rules[ingredient]
            if profile["cookable_from"] == "CHOPPED":
                act("chop", ingredient=ingredient)
            wait_until(target - profile["cook_time"] - config.COOK_START_GS)
            before = {i for i, b in enumerate(e.burners) if b.job}
            act("cook", ingredient=ingredient)
            after = {i for i, b in enumerate(e.burners) if b.job}
            indices[o.order_id] = (after - before).pop()
        for o in batch:
            card = spec.recipe_cards[o.dish]
            ingredient = next(i for i, state in card.items() if state == "COOKED")
            idx = indices[o.order_id]
            job = e.burners[idx].job
            if job is None:
                raise ValueError("missing cooked unit")
            wait_until(job.ready_gs)
            act("collect_cooked", ingredient=ingredient, burner_index=idx)
            for ingredient, state in card.items():
                if state == "COOKED":
                    continue
                act("collect", ingredient=ingredient)
                if state == "CHOPPED":
                    act("chop", ingredient=ingredient)
            act("plate", recipe=o.dish)
            act("serve", order_id=o.order_id)
            active.remove(o.order_id)
    if e.counters["orders_served"] != 4:
        raise ValueError("witness did not fully clear")
    return actions, e, {"peak_active_orders": peak_orders, "first_operation_gs": starts}


def certificate(spec):
    if len(spec.orders) != 4 or any(o.arrival_gs != 0 for o in spec.orders):
        raise ValueError("certificate requires four time-zero orders")
    deadlines = {o.deadline_gs for o in spec.orders}
    if len(deadlines) != 1:
        raise ValueError("certificate requires common deadline")
    deadline = deadlines.pop()
    durations = {}
    used = set()
    for o in spec.orders:
        cooked = [i for i, state in spec.recipe_cards[o.dish].items() if state == "COOKED"]
        if len(cooked) != 1 or cooked[0] in used:
            raise ValueError("certificate requires unique mandatory cooked ingredient per order")
        used.add(cooked[0])
        durations[o.order_id] = spec.ingredient_rules[cooked[0]]["cook_time"]
    work = sum(durations.values())
    # Single cooked unit per order, no initial food or work before arrival, no sharing.
    # Its active interval contains at least that mandatory cook duration. If only k
    # orders were ever active, sum(duration_i) <= k*deadline would be necessary.
    return {"kind": "mandatory-single-cook-active-order-duration-bound-1",
            "deadline_gs": deadline, "order_active_duration_lb_gs": durations,
            "sum_order_active_duration_lb_gs": work,
            "lower_bound_active_orders": math.ceil(work/deadline),
            "mandatory_cook_work_gs": work,
            "lower_bound_burners": math.ceil(work/deadline),
            "preconditions": ["all orders arrive at time zero", "common deadline",
                              "one distinct mandatory cooked ingredient per order",
                              "empty initial inventory and burners", "single-use component units",
                              "advance preparation belongs to consuming order"]}


def task_identity(spec):
    data = env.serialize_spec(spec)
    data.pop("horizon_gs")
    for o in data["orders"]:
        o.pop("deadline_gs")
    return env.digest(data)


def witness_summary(e, actions, overlap):
    return {"orders_served": e.counters["orders_served"], "decisions": len(actions),
            "makespan_gs": round(e.clock_gs, 4),
            "serve_times": serve_times(e),
            "invalid_actions": e.counters["invalid_actions"], "burns": e.counters["burns"],
            "peak_hands": max(len(frame["hands"]) for frame in e.trace),
            "peak_cooking_jobs": max(sum(b["status"] != "FREE" for b in f["burners"]) for f in e.trace),
            "nonwait_actions": sum(a["name"] != "wait" for a in actions),
            "chop_actions": sum(a["name"] == "chop" for a in actions),
            **overlap}


def setting(spec, level):
    return {"level": level, "orders": 4, "burners": 4, "hand_slots": 4,
            "recipe_components": sorted(len(c) for c in spec.recipe_cards.values()),
            "cooked_components_per_order": 1,
            "cook_time_values_gs": [360, 400, 440, 480], "burn_window_gs": 180,
            "deadline_margin_gs": MARGIN_GS[level], "deadline_gs": spec.orders[0].deadline_gs,
            "certified_active_order_lower_bound": REQUIRED_ORDER_LB[level],
            "witness_wave_orders": BATCH_SIZE[level],
            "wait_choices_gs": list(WAITS), "max_decisions": env.MAX_DECISIONS}


def build_rows():
    rows = []
    for seed in SEEDS:
        base = shared_spec(seed)
        for level in LEVELS:
            actions, e, overlap = witness(base, BATCH_SIZE[level])
            deadline = math.ceil(e.clock_gs + MARGIN_GS[level])
            spec = replace(base, orders=[replace(o, deadline_gs=deadline) for o in base.orders],
                           horizon_gs=deadline+30)
            proof = certificate(spec)
            if proof["lower_bound_active_orders"] != REQUIRED_ORDER_LB[level]:
                raise ValueError(f"desired bound not achieved: seed={seed}, level={level}, proof={proof}")
            row = {"arm": f"level-{level}", "level": level, "seed": seed,
                   "split": "development-calibration", "setting": setting(spec, level),
                   "spec": env.serialize_spec(spec), "task_identity": task_identity(spec),
                   "witness_actions": actions, "witness_hash": env.digest(actions),
                   "witness_decisions": len(actions), "witness_makespan": e.clock_gs,
                   "witness_serve_times": serve_times(e),
                   "witness_stats": witness_summary(e, actions, overlap),
                   "capacity_certificate": proof, "oracle_served": None}
            verify_row(row)
            rows.append(row)
    verify_dataset(rows)
    return rows


def verify_row(row):
    install_wait_contract()
    level, seed = row["level"], row["seed"]
    if level not in LEVELS or seed not in SEEDS:
        raise ValueError("undeclared row identity")
    spec = env.deserialize_spec(row["spec"])
    if (row["arm"] != f"level-{level}" or row["split"] != "development-calibration"
            or row["oracle_served"] is not None):
        raise ValueError("row metadata mismatch")
    if task_identity(spec) != row["task_identity"] or row["task_identity"] != task_identity(shared_spec(seed)):
        raise ValueError("shared task identity mismatch")
    if certificate(spec) != row["capacity_certificate"]:
        raise ValueError("certificate mismatch")
    if row["capacity_certificate"]["lower_bound_active_orders"] != REQUIRED_ORDER_LB[level]:
        raise ValueError("order-overlap bound mismatch")
    if env.digest(row["witness_actions"]) != row["witness_hash"]:
        raise ValueError("witness hash mismatch")
    e = VariantEngine(spec, record_trace=True)
    for action in row["witness_actions"]:
        if e.terminated:
            raise ValueError("action after termination")
        e.step([ToolCall(**action)])
    if e.counters["orders_served"] != 4 or e.counters["invalid_actions"] or e.counters["burns"]:
        raise ValueError("unclean witness")
    if len(row["witness_actions"]) >= env.MAX_DECISIONS:
        raise ValueError("witness decision allowance insufficient")
    # Reconstruct the declared policy independently; validates actual order ownership
    # and peak active-order count, not merely a copied stats field.
    actions, regenerated, overlap = witness(spec, BATCH_SIZE[level])
    if actions != row["witness_actions"]:
        raise ValueError("witness policy mismatch")
    stats = witness_summary(regenerated, actions, overlap)
    if stats != row["witness_stats"] or stats["peak_active_orders"] != BATCH_SIZE[level]:
        raise ValueError("witness stats or active-order assignment mismatch")
    if row["witness_decisions"] != len(actions) or row["witness_serve_times"] != serve_times(e):
        raise ValueError("witness metadata mismatch")
    if row["setting"] != setting(spec, level):
        raise ValueError("setting mismatch")
    if spec.orders[0].deadline_gs != math.ceil(e.clock_gs+MARGIN_GS[level]):
        raise ValueError("deadline construction mismatch")
    if spec.horizon_gs != spec.orders[0].deadline_gs+30 or row["witness_makespan"] != e.clock_gs:
        raise ValueError("horizon or witness makespan mismatch")


def verify_dataset(rows):
    if len(rows) != len(LEVELS)*len(SEEDS) or {(r["level"], r["seed"]) for r in rows} != {(l,s) for l in LEVELS for s in SEEDS}:
        raise ValueError("dataset membership mismatch")
    for seed in SEEDS:
        paired = sorted([r for r in rows if r["seed"] == seed], key=lambda r:r["level"])
        if len({r["task_identity"] for r in paired}) != 1:
            raise ValueError("pressure levels do not share same task")
        deadlines = [r["setting"]["deadline_gs"] for r in paired]
        if not deadlines[0] > deadlines[1] > deadlines[2]:
            raise ValueError("deadline path not strictly monotonic")
        for row in paired:
            verify_row(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--preview", action="store_true", help="construct/replay without writing")
    args = parser.parse_args()
    if args.verify:
        rows = json.loads(OUT.read_text())
        verify_dataset(rows)
        print(f"Verified {len(rows)} paired diverse kitchens: serial L0, certified ≥2-order L1, ≥3-order L2.")
        return
    rows = build_rows()
    if not args.preview:
        if OUT.exists():
            raise ValueError("dataset exists; use --verify")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(rows, indent=2, sort_keys=True)+"\n")
    print(json.dumps([{k: r[k] for k in ("seed", "level", "setting", "witness_stats", "capacity_certificate")} for r in rows], indent=2))


if __name__ == "__main__":
    main()

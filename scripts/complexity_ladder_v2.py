#!/usr/bin/env python3
"""Generate a replay-certified, parameterized Expert workload ladder.

The one exposed parameter is burner load (4, 6, or 8). All levels share the
same 9x9 room, three-component recipes, action contract, and timer policy.
Orders = 1.5 * burners, with two required cooks per order. Cook and burn times
scale by a declared formula so one chef has time to handle larger batches.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from kitchenrush import config  # noqa: E402
from kitchenrush.procgen import OrderSpec, StationSpec  # noqa: E402
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2 import environment as env  # noqa: E402
from concurrency_probe_v2 import serve_times  # noqa: E402
from difficulty_sweep_v2 import VariantEngine  # noqa: E402

NAME = "complexity-ladder-pilot-20260924"
OUT = ROOT / "data/v2" / NAME / "kitchens.json"
LEVELS = (4, 6, 8)
SEEDS = (90301, 90302)
WAITS = (1, 2, 5, 10, 20, 50, 100)
ROOM = 9
MARGIN_GS = 15


def install_wait_contract() -> None:
    env.WAITS = WAITS


def setting(level: int) -> dict:
    if level not in LEVELS:
        raise ValueError("undeclared complexity level")
    return {
        "level": level, "burners": level, "orders": level * 3 // 2,
        "recipe_components": 3, "cooked_components_per_order": 2,
        "cook_time_gs": 100 + 40 * level,
        "burn_window_gs": 40 + 12 * level,
        "deadline_margin_gs": MARGIN_GS,
        "room_grid_n": ROOM, "hand_slots": config.HAND_SLOTS,
        "wait_choices_gs": list(WAITS), "max_decisions": env.MAX_DECISIONS,
    }


def stations(level: int) -> list[StationSpec]:
    # Perimeter cells excluding corners all have one reachable interior neighbor.
    ring = ([(0, c) for c in range(1, ROOM - 1)]
            + [(r, ROOM - 1) for r in range(1, ROOM - 1)]
            + [(ROOM - 1, c) for c in range(ROOM - 2, 0, -1)]
            + [(r, 0) for r in range(ROOM - 2, 0, -1)])
    kinds = ([(config.ING, ingredient) for ingredient in sorted(config.INGREDIENTS)]
             + [(config.BOARD, None), (config.PLATE, None),
                (config.PASS, None), (config.BIN, None)]
             + [(config.STOVE, None)] * level)
    if len(kinds) > len(ring):
        raise ValueError("room has too few reachable station cells")
    return [StationSpec(cell, kind, ingredient)
            for cell, (kind, ingredient) in zip(ring, kinds, strict=False)]


def make_spec(level: int, seed: int):
    chosen = setting(level)
    base = env.generate(seed, "main", 3)
    rng = random.Random(f"complexity-ladder:{seed}:{level}")
    cookables = ("broth_base", "egg", "mushroom", "noodles", "patty")
    raw_sides = ("bun", "cheese", "lettuce", "tomato")
    possibilities = [dict(sorted((*((a, "COOKED"), (b, "COOKED")), (side, "RAW"))))
                     for a, b in itertools.combinations(cookables, 2)
                     for side in raw_sides]
    cards = {f"R{i}": card for i, card in enumerate(rng.sample(possibilities, chosen["orders"]), 1)}
    layout = stations(level)
    occupied = {item.cell for item in layout}
    blocked = frozenset((r, c) for r in range(ROOM) for c in range(ROOM)
                        if (r in (0, ROOM - 1) or c in (0, ROOM - 1))
                        and (r, c) not in occupied)
    profiles = {name: dict(value) for name, value in base.ingredient_rules.items()}
    for value in profiles.values():
        if value["cookable_from"]:
            value["cook_time"] = chosen["cook_time_gs"]
            value["burn_window"] = chosen["burn_window_gs"]
    profiles["mushroom"]["cookable_from"] = "RAW"
    return replace(base, tier=f"v2-complexity-{level}", grid_n=ROOM,
                   burner_count=level, horizon_gs=5000, stations=layout,
                   blocked=blocked, door=(ROOM - 1, ROOM // 2),
                   chef_start=(ROOM // 2, ROOM // 2),
                   active_recipes=tuple(cards), recipe_cards=cards,
                   ingredient_rules=profiles,
                   orders=[OrderSpec(f"O{i}", f"R{i}", 0, 4000, 1)
                           for i in range(1, chosen["orders"] + 1)])


def witness(spec) -> tuple[list[dict], VariantEngine]:
    install_wait_contract()
    engine = VariantEngine(spec, record_trace=True)
    actions = []

    def act(name, **arguments):
        call = ToolCall(name, arguments)
        engine.step([call])
        actions.append(asdict(call))
        if engine.counters["invalid_actions"] or engine.counters["burns"]:
            raise ValueError(f"bad witness action {name} {arguments} at {engine.clock_gs}")

    # Each wave fills every burner with two components for each of B/2 orders.
    for first in range(1, len(spec.orders) + 1, spec.burner_count // 2):
        batch = list(range(first, first + spec.burner_count // 2))
        jobs = {}
        for number in batch:
            for ingredient, state in spec.recipe_cards[f"R{number}"].items():
                if state != "COOKED":
                    continue
                act("collect", ingredient=ingredient)
                before = {i for i, burner in enumerate(engine.burners) if burner.job}
                act("cook", ingredient=ingredient)
                after = {i for i, burner in enumerate(engine.burners) if burner.job}
                added = after - before
                if len(added) != 1:
                    raise ValueError("cook did not occupy one new burner")
                jobs[(number, ingredient)] = added.pop()
        for number in batch:
            card = spec.recipe_cards[f"R{number}"]
            for ingredient, state in card.items():
                if state != "COOKED":
                    continue
                index = jobs[(number, ingredient)]
                while True:
                    burner = engine.burners[index]
                    if burner.job is None:
                        raise ValueError("job burned before pickup")
                    remaining = burner.job.ready_gs - engine.clock_gs
                    if remaining <= 0:
                        break
                    act("wait", seconds=max(value for value in WAITS
                                            if value <= max(1, remaining)))
                act("collect_cooked", ingredient=ingredient, burner_index=index)
            for ingredient, state in card.items():
                if state == "RAW":
                    act("collect", ingredient=ingredient)
            act("plate", recipe=f"R{number}")
            act("serve", order_id=f"O{number}")
    if engine.counters["orders_served"] != len(spec.orders):
        raise ValueError("witness did not complete kitchen")
    return actions, engine


def certificate(spec) -> dict:
    deadlines = {order.deadline_gs for order in spec.orders}
    if len(deadlines) != 1 or any(order.arrival_gs != 0 for order in spec.orders):
        raise ValueError("shared-deadline certificate preconditions failed")
    deadline = deadlines.pop()
    work = sum(spec.ingredient_rules[ingredient]["cook_time"]
               for order in spec.orders
               for ingredient, state in spec.recipe_cards[order.dish].items()
               if state == "COOKED")
    return {"mandatory_cook_work_gs": work,
            "deadline_gs": deadline,
            "lower_bound_burners": math.ceil(work / deadline),
            "one_fewer_burner_overload_gs": work - (spec.burner_count - 1) * deadline,
            "single_burner_overload_gs": work - deadline}


def make_row(level: int, seed: int) -> dict:
    spec = make_spec(level, seed)
    actions, engine = witness(spec)
    deadline = math.ceil(max(serve_times(engine).values()) + MARGIN_GS)
    spec = replace(spec, horizon_gs=deadline + 30,
                   orders=[replace(order, deadline_gs=deadline) for order in spec.orders])
    proof = certificate(spec)
    if proof["lower_bound_burners"] < max(3, level - 1):
        raise ValueError(f"level {level} did not create enough concurrent pressure: {proof}")
    row = {"arm": f"level-{level}", "level": level, "seed": seed,
           "setting": setting(level), "spec": env.serialize_spec(spec),
           "witness_actions": actions, "witness_hash": env.digest(actions),
           "witness_decisions": len(actions), "witness_serve_times": serve_times(engine),
           "witness_makespan": engine.clock_gs, "capacity_certificate": proof,
           "oracle_served": None}
    verify_row(row)
    return row


def verify_row(row: dict) -> None:
    install_wait_contract()
    spec = env.deserialize_spec(row["spec"])
    if row["setting"] != setting(row["level"]) or env.digest(row["witness_actions"]) != row["witness_hash"]:
        raise ValueError("setting or witness mismatch")
    if certificate(spec) != row["capacity_certificate"]:
        raise ValueError("capacity certificate mismatch")
    engine = VariantEngine(spec, record_trace=True)
    for action in row["witness_actions"]:
        if engine.terminated:
            raise ValueError("witness includes action after termination")
        engine.step([ToolCall(**action)])
    if (engine.counters["orders_served"] != len(spec.orders)
            or engine.counters["invalid_actions"] or engine.counters["burns"]):
        raise ValueError("witness does not cleanly replay")
    if len(row["witness_actions"]) >= env.MAX_DECISIONS:
        raise ValueError("witness exceeds decision limit")
    peak = max(sum(b["status"] != "FREE" for b in frame["burners"])
               for frame in engine.trace)
    if peak != row["level"]:
        raise ValueError("witness did not exercise every burner")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        rows = json.loads(OUT.read_text())
        for row in rows:
            verify_row(row)
        print(f"verified {len(rows)} kitchens")
        return
    if OUT.exists():
        raise ValueError("frozen ladder dataset already exists")
    rows = [make_row(level, seed) for level in LEVELS for seed in SEEDS]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    print(json.dumps([{"level": r["level"], "seed": r["seed"],
                       "orders": r["setting"]["orders"],
                       "witness_decisions": r["witness_decisions"],
                       "deadline": r["capacity_certificate"]["deadline_gs"],
                       "minimum_burners": r["capacity_certificate"]["lower_bound_burners"]}
                      for r in rows], indent=2))


if __name__ == "__main__":
    main()

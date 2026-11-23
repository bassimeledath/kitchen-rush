#!/usr/bin/env python3
"""Build exploratory kitchens with a certified one-order-at-a-time timing gap.

This leaves the frozen v2 protocol alone. A replayed overlap witness proves the
kitchen is solvable. The serial lower bound ignores walking, arrivals, and most
dependency constraints, so a positive gap is a sound (conservative) proof that
no one-order-at-a-time schedule can meet every deadline. Beam-search nonfinds
are never used as proof.
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
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kitchenrush import config  # noqa: E402
from kitchenrush.procgen import OrderSpec  # noqa: E402
from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2.environment import (  # noqa: E402
    MAX_DECISIONS, deserialize_spec, digest, recipe_pool, serialize_spec,
)
from concurrency_probe_v2 import run_beam, serve_times  # noqa: E402
from difficulty_sweep_v2 import VariantEngine, build  # noqa: E402

NAME = "overlap-gap-pilot-20260924"
OUT = ROOT / "data/v2" / NAME / "kitchens.json"
COOK = (50, 70)
BURN = (10, 15)
MIN_COOKED = 1
SIZE = 3
WIDTH = 100
MIN_SERIAL_GAP = 5.0
# The first two tickets share a deadline. We find a winning schedule under a
# proven serial-impossible deadline, then leave this many game seconds after
# its completed serve. The medium/hard mix is fixed before model results.
LEVELS = (("medium", 20.0), ("hard", 10.0))
MIN_WITNESS_MARGIN = 9.0
MAX_WITNESS_DECISIONS = 80
THIRD_ARRIVAL = 75.0
THIRD_DEADLINE_PAD = 100.0
THIRD_EXTRA_MARGIN = 10.0


def pre_serve_lower_bound(spec, order) -> float:
    """Optimistic elapsed time from starting this order to starting its serve.

    Every component must be collected and processed, then the dish plated. The
    sum of action durations is mandatory. Separately, the longest cooked
    component needs its full cook time on its own critical path. Their maximum
    is a lower bound even with two burners and zero walking time.
    """
    required_actions = config.PLATE_GS
    longest_path = 0.0
    for ingredient, state in spec.recipe_cards[order.dish].items():
        profile = config.Ingredient(**spec.ingredient_rules[ingredient])
        before_cook = config.COLLECT_GS
        if state == "CHOPPED" or (state == "COOKED" and profile.cookable_from == "CHOPPED"):
            before_cook += config.CHOP_GS
        component_actions = before_cook
        if state == "COOKED":
            component_actions += config.COOK_START_GS + config.COOK_PICKUP_GS
            longest_path = max(
                longest_path,
                before_cook + config.COOK_START_GS + profile.cook_time
                + config.COOK_PICKUP_GS + config.PLATE_GS,
            )
        required_actions += component_actions
    return max(required_actions, longest_path)


def serial_gap_certificate(spec) -> dict:
    """Lower bound on the missed deadline in every serial order permutation.

    Serial means no component work for a second order before the current one is
    served. We optimistically allow zero walking and ignore order arrival times.
    At least one order must start its serve strictly before its deadline. An
    order's three-second serve action can finish after the deadline, but the next
    order cannot start until that action ends.
    """
    per_order = {o.order_id: pre_serve_lower_bound(spec, o) for o in spec.orders}
    best_case = None
    for permutation in itertools.permutations(spec.orders):
        elapsed = 0.0
        largest_miss = -math.inf
        sequence = []
        for order in permutation:
            serve_start_lb = elapsed + per_order[order.order_id]
            miss = serve_start_lb - order.deadline_gs
            largest_miss = max(largest_miss, miss)
            sequence.append([order.order_id, serve_start_lb, order.deadline_gs])
            elapsed = serve_start_lb + config.SERVE_GS
        if best_case is None or largest_miss < best_case["minimum_unavoidable_miss_gs"]:
            best_case = {
                "minimum_unavoidable_miss_gs": largest_miss,
                "best_serial_order": [item[0] for item in sequence],
                "best_serial_lower_bound": sequence,
            }
    return {"per_order_pre_serve_lower_bound_gs": per_order, **best_case}


def replay(spec, actions):
    engine = VariantEngine(spec, record_trace=True)
    for action in actions:
        if engine.terminated:
            raise ValueError("witness contains action after termination")
        engine.step([ToolCall(**action)])
    return engine


def sprint_spec(seed: int, serial_gap: float):
    base = build(
        seed, split="main", size=SIZE, cook=COOK, burn=BURN,
        min_cooked=MIN_COOKED,
    )
    if base is None:
        return None
    groups = {}
    for card in recipe_pool(SIZE, "main"):
        cooked = [ingredient for ingredient, state in card.items() if state == "COOKED"]
        if len(cooked) == 1:
            groups.setdefault(cooked[0], []).append(card)
    if len(groups) < 3:
        return None
    rng = random.Random(f"overlap-sprint-cards:{seed}")
    ingredients = rng.sample(sorted(groups), 3)
    cards = {
        f"R{i + 1}": dict(rng.choice(groups[ingredient]))
        for i, ingredient in enumerate(ingredients)
    }
    orders = [
        OrderSpec(f"O{i + 1}", f"R{i + 1}", 0.0 if i < 2 else THIRD_ARRIVAL,
                  1000.0, 1.0)
        for i in range(3)
    ]
    spec = replace(
        base, tier="v2-overlap-sprint-main-3", active_recipes=tuple(cards),
        recipe_cards=cards, orders=orders,
    )
    earliest_serial_second = (
        pre_serve_lower_bound(spec, orders[0]) + config.SERVE_GS
        + pre_serve_lower_bound(spec, orders[1])
    )
    first_deadline = math.floor(earliest_serial_second - serial_gap)
    return replace(spec, orders=[
        replace(order, deadline_gs=(first_deadline if i < 2 else first_deadline + THIRD_DEADLINE_PAD))
        for i, order in enumerate(orders)
    ])


def candidate(seed: int, level: str, witness_cushion: float, width: int = WIDTH,
              diagnostic: dict | None = None) -> dict | None:
    def reject(reason):
        if diagnostic is not None:
            diagnostic["reason"] = reason
        return None

    spec = sprint_spec(seed, MIN_SERIAL_GAP)
    if spec is None:
        return reject("generator_pool")
    certificate = serial_gap_certificate(spec)
    if certificate["minimum_unavoidable_miss_gs"] < MIN_SERIAL_GAP:
        return reject("serial_gap_too_small")
    found = run_beam(
        spec, width=width, max_depth=MAX_WITNESS_DECISIONS,
        engine_cls=VariantEngine, cross_order=True, unbound=False,
        record_trace=False,
    )
    if found is None:
        return reject("no_overlap_witness_found")
    _, path, solved = found
    if len(path) > MAX_WITNESS_DECISIONS or solved.counters["invalid_actions"]:
        return reject("invalid_or_overlong_witness")
    served_at = serve_times(solved)
    if len(served_at) != len(spec.orders):
        return reject("incomplete_witness_events")
    first_deadline = math.ceil(max(served_at["O1"], served_at["O2"]) + witness_cushion)
    if first_deadline > spec.orders[0].deadline_gs:
        return reject("overlap_serial_gap_too_narrow")
    third_deadline = math.ceil(served_at["O3"] + witness_cushion + THIRD_EXTRA_MARGIN)
    spec = replace(spec, orders=[
        replace(order, deadline_gs=(first_deadline if order.order_id in ("O1", "O2") else third_deadline))
        for order in spec.orders
    ])
    certificate = serial_gap_certificate(spec)
    if diagnostic is not None:
        diagnostic["serial_gap_gs"] = certificate["minimum_unavoidable_miss_gs"]
    if certificate["minimum_unavoidable_miss_gs"] < MIN_SERIAL_GAP:
        return reject("repriced_serial_gap_too_small")
    witness_margin = min(order.deadline_gs - served_at[order.order_id] for order in spec.orders)
    if diagnostic is not None:
        diagnostic["witness_margin_gs"] = round(witness_margin, 3)
        diagnostic["witness_decisions"] = len(path)
    if witness_margin < MIN_WITNESS_MARGIN:
        return reject("overlap_witness_margin_too_small")
    actions = [asdict(call) for call in path]
    verified = replay(spec, actions)
    if verified.counters["orders_served"] != len(spec.orders) or verified.counters["invalid_actions"]:
        return reject("deadline_witness_replay_failed")
    peak_burners = max(
        sum(burner["status"] != "FREE" for burner in frame["burners"])
        for frame in verified.trace
    )
    if peak_burners < 2:
        return reject("no_two_burner_overlap")
    return {
        "arm": f"gap-{level}", "seed": seed,
        "setting": {
            "size": SIZE, "min_cooked": MIN_COOKED,
            "cook": COOK, "burn": BURN,
            "witness_deadline_cushion_gs": witness_cushion,
            "proven_serial_gap_gs": certificate["minimum_unavoidable_miss_gs"],
            "witness_min_margin_gs": witness_margin,
            "third_arrival_gs": THIRD_ARRIVAL,
        },
        "spec": serialize_spec(spec),
        "witness_actions": actions,
        "witness_hash": digest(actions),
        "witness_decisions": len(actions),
        "witness_makespan": verified.clock_gs,
        "witness_serve_times": served_at,
        "peak_busy_burners": peak_burners,
        "serial_certificate": certificate,
        "oracle_served": None,
    }


def build_dataset(start: int, count: int, scan: int, width: int) -> list[dict]:
    rows = []
    for seed in range(start, start + scan):
        level, cushion = LEVELS[len(rows) % len(LEVELS)]
        diagnostic = {}
        row = candidate(seed, level, cushion, width, diagnostic)
        print(json.dumps({
            "seed": seed, "accepted": row is not None,
            "level": level, **diagnostic,
        }), flush=True)
        if row is not None:
            rows.append(row)
        if len(rows) >= count:
            break
    return rows


def verify_dataset(rows: list[dict]) -> None:
    for row in rows:
        spec = deserialize_spec(row["spec"])
        if digest(row["witness_actions"]) != row["witness_hash"]:
            raise ValueError("witness hash mismatch")
        witness = replay(spec, row["witness_actions"])
        if witness.counters["orders_served"] != len(spec.orders) or witness.counters["invalid_actions"]:
            raise ValueError("witness does not solve saved spec")
        certificate = serial_gap_certificate(spec)
        if certificate != row["serial_certificate"] or certificate["minimum_unavoidable_miss_gs"] < MIN_SERIAL_GAP:
            raise ValueError("serial certificate does not verify")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=88000)
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--scan", type=int, default=24)
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError(f"output already exists: {args.out}")
    rows = build_dataset(args.start, args.count, args.scan, args.width)
    if len(rows) != args.count:
        raise RuntimeError(f"only {len(rows)}/{args.count} certified kitchens; output not written")
    verify_dataset(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.out), "accepted": len(rows),
                      "levels": [row["arm"] for row in rows]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

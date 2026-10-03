#!/usr/bin/env python3
"""Find generator settings where OVERLAPPING play is necessary, not merely allowed.

Offline; costs nothing; touches no frozen source. environment.py is covered by the protocol
hash, so every variant here is built by a local copy of its generator with knobs exposed.
Nothing is promoted into the real generator until a setting proves out.

The prize available to an overlapping schedule is bounded by how much COOK time it can hide
behind other work. At the shipped settings (cook_time 4-12s, cooked components left to
chance) that prize is a median 14% of makespan and as little as 6% -- too thin for any
deadline to separate an overlapping player from a serial one. The knobs swept here:

  --cook-lo/--cook-hi   cook duration range (the hideable time)
  --min-cooked          least cooked components per recipe (guarantees something to hide)
  --burn-lo/--burn-hi   burn window (how punishing it is to start early and wander off)

For each setting we report the serial oracle's makespan, the overlapping witness's makespan,
and the ratio. A ratio near 1.0 means no deadline can ever require concurrency; the lower the
ratio, the wider the band of deadlines that admit overlap and reject serial play.

  sweep [--seeds N] [--jobs J]
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kitchenrush import config  # noqa: E402
from kitchenrush.engine import KitchenRushEngine  # noqa: E402
from kitchenrush.procgen import OrderSpec, StationSpec  # noqa: E402
from kitchenrush.v2.environment import (  # noqa: E402
    SPLITS,
    Engine,
    Kitchen,
    digest,
    recipe_pool,
)
from concurrency_probe_v2 import run_beam, run_serial, serve_times  # noqa: E402


class VariantEngine(Engine):
    """Engine with the shipped timer-range guard lifted, for exploration only.

    environment.py:221 rejects any cook_time outside 4-12s or burn_window outside 3-8s.
    That guard is correct for the frozen protocol and is exactly the setting under test
    here, so this subclass reproduces the profile load without it. Nothing in this file is
    ever used to score a model -- promoting a setting means editing the real generator and
    re-freezing the protocol hash."""

    def __init__(self, spec, *, record_trace=False):
        KitchenRushEngine.__init__(self, spec, record_trace=record_trace)
        for name, profile in getattr(spec, "ingredient_rules", {}).items():
            self.ingredient_rules[name] = config.Ingredient(**profile)
        self.active_ingredients = {s.ingredient for s in spec.stations if s.type == config.ING}


def profiles_for(seed, split, cook, burn):
    """environment.generate_profiles with the timer ranges exposed."""
    rng = random.Random(f"ingredient-profiles:{seed}")
    while True:
        out = {}
        for name, item in sorted(config.INGREDIENTS.items()):
            if item.cookable_from:
                item = replace(
                    item,
                    cook_time=rng.randint(*cook),
                    burn_window=rng.randint(*burn),
                )
                if name == "mushroom":
                    item = replace(item, cookable_from=rng.choice(["RAW", "CHOPPED"]))
            out[name] = asdict(item)
        if int(digest(out), 16) % 10 in SPLITS[split]:
            return out


def build(seed, split="main", size=3, *, cook=(4, 12), burn=(3, 8), min_cooked=0, slack=1.35):
    """A local copy of environment.generate with the difficulty knobs exposed."""
    rng = random.Random(seed)
    profiles = profiles_for(seed, split, cook, burn)
    pool = recipe_pool(size, split)
    if min_cooked:
        pool = [c for c in pool if sum(s == "COOKED" for s in c.values()) >= min_cooked]
        if len(pool) < 3:
            return None
    cards = {f"R{i + 1}": dict(c) for i, c in enumerate(rng.sample(pool, 3))}

    stations = []
    cells = [(0, c) for c in range(1, 6)] + [(r, 6) for r in range(1, 6)]
    cells += [(6, c) for c in range(5, 0, -1)] + [(r, 0) for r in range(5, 0, -1)]
    kinds = [(config.ING, i) for i in sorted(config.INGREDIENTS)]
    kinds += [
        (config.BOARD, None),
        (config.STOVE, None),
        (config.STOVE, None),
        (config.PLATE, None),
        (config.PASS, None),
        (config.BIN, None),
    ]
    for cell, (kind, ing) in zip(cells, kinds, strict=False):
        stations.append(StationSpec(cell, kind, ing))
    used = {s.cell for s in stations}
    blocked = frozenset(
        (r, c)
        for r in range(7)
        for c in range(7)
        if (r in (0, 6) or c in (0, 6)) and (r, c) not in used
    )

    order_cards = list(cards)
    rng.shuffle(order_cards)
    orders, finish = [], 0.0
    for idx, rid in enumerate(order_cards):
        arrival = 0.0 if idx < 2 else float(rng.randint(35, 65))
        intrinsic = config.PLATE_GS + config.SERVE_GS + 2.4
        for ing, state in cards[rid].items():
            item = config.Ingredient(**profiles[ing])
            intrinsic += config.COLLECT_GS + 1.2
            if state == "CHOPPED" or (state == "COOKED" and item.cookable_from == "CHOPPED"):
                intrinsic += config.CHOP_GS + 1.2
            if state == "COOKED":
                intrinsic += config.COOK_START_GS + item.cook_time + config.COOK_PICKUP_GS + 2.4
        finish = max(arrival, finish) + intrinsic
        orders.append(OrderSpec(f"O{idx + 1}", rid, arrival, math.ceil(finish * slack + 10), 1.0))

    return Kitchen(
        seed=seed,
        tier=f"v2-{split}-{size}",
        grid_n=7,
        burner_count=2,
        horizon_gs=600,
        show_ready_actions=False,
        active_recipes=tuple(cards),
        stations=stations,
        chef_start=(3, 3),
        orders=orders,
        recipe_cards=cards,
        latency_budget=0,
        blocked=blocked,
        door=(6, 3),
        ingredient_rules=profiles,
    )


def one(job):
    seed, setting, width = job
    spec = build(seed, **setting)
    if spec is None:
        return None
    oracle, oracle_dec = run_serial(spec, engine_cls=VariantEngine)
    oracle_ok = oracle.counters["orders_served"] == len(spec.orders)
    strict = run_beam(spec, width=width, engine_cls=VariantEngine, cross_order=False)
    found = run_beam(spec, width=width, engine_cls=VariantEngine, cross_order=True)
    if not oracle_ok or found is None or strict is None:
        return dict(seed=seed, setting=setting, ratio=None)
    beam_span, beam_path, beam_engine = found
    strict_span, strict_path, strict_engine = strict
    return dict(
        seed=seed,
        setting=setting,
        oracle_makespan=oracle.clock_gs,
        oracle_decisions=oracle_dec,
        oracle_serves=serve_times(oracle),
        serial_makespan=strict_span,
        serial_decisions=len(strict_path),
        serial_serves=serve_times(strict_engine),
        beam_makespan=beam_span,
        beam_decisions=len(beam_path),
        beam_serves=serve_times(beam_engine),
        ratio=beam_span / strict_span,
        oracle_ratio=beam_span / oracle.clock_gs,
        cooked_per_card=[
            sum(s == "COOKED" for s in c.values()) for c in spec.recipe_cards.values()
        ],
    )


SETTINGS = [
    dict(cook=(4, 12), burn=(3, 8), min_cooked=0),   # shipped
    dict(cook=(4, 12), burn=(3, 8), min_cooked=2),
    dict(cook=(15, 30), burn=(3, 8), min_cooked=0),
    dict(cook=(15, 30), burn=(3, 8), min_cooked=2),
    dict(cook=(25, 45), burn=(3, 8), min_cooked=2),
    dict(cook=(25, 45), burn=(8, 16), min_cooked=2),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--start", type=int, default=85000)
    ap.add_argument("--width", type=int, default=200)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default="/tmp/kr-difficulty-sweep.json")
    args = ap.parse_args()

    seeds = list(range(args.start, args.start + args.seeds))
    jobs = [(s, setting, args.width) for setting, s in itertools.product(SETTINGS, seeds)]
    rows = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for row in pool.map(one, jobs):
            if row:
                rows.append(row)

    print(f"{'cook':>10} {'burn':>8} {'min_ck':>7} | {'oracle':>7} {'serial*':>8} "
          f"{'overlap':>8} {'ratio':>6} {'saves':>6} {'n':>3}")
    print("-" * 72)
    summary = []
    for setting in SETTINGS:
        got = [r for r in rows if r["setting"] == setting and r.get("ratio")]
        failed = [r for r in rows if r["setting"] == setting and not r.get("ratio")]
        if not got:
            print(f"{str(setting['cook']):>10} {str(setting['burn']):>8} "
                  f"{setting['min_cooked']:>7} |   no feasible kitchens ({len(failed)} failed)")
            continue
        ratios = [r["ratio"] for r in got]
        line = dict(
            setting=setting,
            median_ratio=statistics.median(ratios),
            worst_ratio=max(ratios),
            best_ratio=min(ratios),
            oracle_median=statistics.median(r["oracle_makespan"] for r in got),
            serial_median=statistics.median(r["serial_makespan"] for r in got),
            beam_median=statistics.median(r["beam_makespan"] for r in got),
            n=len(got),
            infeasible=len(failed),
        )
        summary.append(line)
        print(f"{str(setting['cook']):>10} {str(setting['burn']):>8} {setting['min_cooked']:>7} "
              f"| {line['oracle_median']:7.1f} {line['serial_median']:8.1f} "
              f"{line['beam_median']:8.1f} "
              f"{line['median_ratio']:6.3f} {100 * (1 - line['median_ratio']):5.1f}% {len(got):>3}")
    Path(args.out).write_text(json.dumps(dict(rows=rows, summary=summary), indent=2, default=str))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
